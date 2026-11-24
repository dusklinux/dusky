#!/usr/bin/env python3
"""
Comprehensive standalone stress-test & verification suite for
218_hibernation_setup.py on Arch Linux test VMs.

Usage:
    sudo python3 test_218_hibernation.py
    sudo python3 test_218_hibernation.py --script /path/to/218_hibernation_setup.py
    sudo python3 test_218_hibernation.py --keep-backups
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Callable, Sequence

# ---------------------------------------------------------------------------
# System & Target Paths
# ---------------------------------------------------------------------------

DEFAULT_SCRIPT_PATH = Path(
    "/home/dusk/user_scripts/arch_setup_scripts/scripts/218_hibernation_setup.py"
)

SWAP_SUBVOL = Path("/swap")
SWAPFILE_PATH = SWAP_SUBVOL / "swapfile"
FSTAB_PATH = Path("/etc/fstab")
FSTAB_COMMENT = "# Btrfs swapfile for system hibernation"

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

PROC_SWAPS = Path("/proc/swaps")
SYS_POWER_STATE = Path("/sys/power/state")
SYS_POWER_MEM_SLEEP = Path("/sys/power/mem_sleep")
SYS_KERNEL_LOCKDOWN = Path("/sys/kernel/security/lockdown")

GIB = 1024**3


# ---------------------------------------------------------------------------
# Zero-Dependency ANSI Formatting (NO_COLOR & TTY Aware)
# ---------------------------------------------------------------------------


class Style:
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

    def banner(self, text: str) -> None:
        line = "=" * 72
        print(f"\n{self.bold}{self.cyan}{line}\n  {text}\n{line}{self.reset}")

    def phase(self, number: int, title: str) -> None:
        print(f"\n{self.bold}{self.cyan}[PHASE {number}] {title}{self.reset}")

    def info(self, text: str) -> None:
        print(f"  {self.blue}[INFO]{self.reset} {text}")

    def pass_msg(self, text: str) -> None:
        print(f"  {self.green}[PASS]{self.reset} {text}")

    def warn(self, text: str) -> None:
        print(f"  {self.yellow}[WARN]{self.reset} {text}")

    def fail(self, text: str) -> None:
        print(f"  {self.red}[FAIL]{self.reset} {text}", file=sys.stderr)


ui = Style()


class TestFailure(AssertionError):
    """Raised when a test verification assertion fails."""


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise TestFailure(message)


# ---------------------------------------------------------------------------
# System Inspection & Snapshot Utilities
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_dir_tree(directory: Path) -> dict[str, tuple[int, str]]:
    """Return {relative_path: (mode, sha256_or_meta)} for all entries in a directory."""
    state: dict[str, tuple[int, str]] = {}
    if not directory.exists():
        return state
    for entry in sorted(directory.iterdir()):
        try:
            st = entry.lstat()
        except OSError:
            continue
        mode = stat.S_IMODE(st.st_mode)
        if entry.is_file() and not entry.is_symlink():
            # Avoid hashing gigabyte swapfiles; hash metadata for large files (>16 MiB)
            if st.st_size > 16 * 1024**2:
                digest = f"largefile:size={st.st_size}:ino={st.st_ino}:mtime_ns={st.st_mtime_ns}"
            else:
                digest = sha256_file(entry)
        elif entry.is_symlink():
            digest = f"symlink:{os.readlink(entry)}"
        else:
            digest = f"dir:ino={st.st_ino}"
        state[entry.name] = (mode, digest)
    return state


@dataclass(frozen=True)
class SystemSnapshot:
    fstab_hash: str | None
    fstab_backups: tuple[str, ...]
    boot_entries: dict[str, tuple[int, str]]
    limine_dropins: dict[str, tuple[int, str]]
    mkinitcpio_dropins: dict[str, tuple[int, str]]
    swap_exists: bool
    swap_mounted: bool
    swap_contents: dict[str, tuple[int, str]]
    sleep_hooks: dict[str, tuple[int, str] | None]
    markers: dict[str, str | None]
    proc_swaps: str


def is_mountpoint(path: Path) -> bool:
    if not path.exists():
        return False
    if findmnt := shutil.which("findmnt"):
        res = subprocess.run(
            [findmnt, "-M", str(path)],
            check=False,
            text=True,
            capture_output=True,
        )
        if res.returncode == 0:
            return True
    try:
        return os.path.ismount(path)
    except OSError:
        return False


def list_fstab_backups() -> tuple[str, ...]:
    return tuple(sorted(p.name for p in Path("/etc").glob("fstab.bak.*")))


def capture_snapshot() -> SystemSnapshot:
    sleep_hooks: dict[str, tuple[int, str] | None] = {}
    for hook in (SLEEP_HOOK_KBD, SLEEP_HOOK_FPRINTD):
        if hook.exists():
            sleep_hooks[hook.name] = (
                stat.S_IMODE(hook.stat().st_mode),
                sha256_file(hook),
            )
        else:
            sleep_hooks[hook.name] = None

    markers: dict[str, str | None] = {}
    for marker in (DUSKY_REBUILD_MARKER, OMARCHY_REBUILD_MARKER):
        markers[str(marker)] = sha256_file(marker) if marker.exists() else None

    proc_swaps_text = PROC_SWAPS.read_text(encoding="utf-8") if PROC_SWAPS.exists() else ""

    return SystemSnapshot(
        fstab_hash=sha256_file(FSTAB_PATH) if FSTAB_PATH.exists() else None,
        fstab_backups=list_fstab_backups(),
        boot_entries=snapshot_dir_tree(SYSTEMD_BOOT_ENTRIES_DIR),
        limine_dropins=snapshot_dir_tree(LIMINE_DROPIN_DIR),
        mkinitcpio_dropins=snapshot_dir_tree(MKINITCPIO_DROPIN_DIR),
        swap_exists=SWAP_SUBVOL.exists(),
        swap_mounted=is_mountpoint(SWAP_SUBVOL),
        swap_contents=snapshot_dir_tree(SWAP_SUBVOL),
        sleep_hooks=sleep_hooks,
        markers=markers,
        proc_swaps=proc_swaps_text,
    )


def parse_swaps_config(swaps_text: str) -> dict[str, tuple[str, int, int]]:
    """Parse /proc/swaps into {filename: (type, size_kib, priority)}, ignoring dynamic used_kib."""
    res = {}
    for line in swaps_text.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 5:
            res[parts[0]] = (parts[1], int(parts[2]), int(parts[4]))
    return res


def diff_snapshots(before: SystemSnapshot, after: SystemSnapshot) -> list[str]:
    diffs: list[str] = []
    if before.fstab_hash != after.fstab_hash:
        diffs.append("/etc/fstab content changed")
    if before.fstab_backups != after.fstab_backups:
        diffs.append(
            f"/etc/fstab.bak.* changed: {before.fstab_backups} -> {after.fstab_backups}"
        )
    if before.boot_entries != after.boot_entries:
        diffs.append("/boot/loader/entries/ changed")
    if before.limine_dropins != after.limine_dropins:
        diffs.append("/etc/limine-entry-tool.d/ changed")
    if before.mkinitcpio_dropins != after.mkinitcpio_dropins:
        diffs.append("/etc/mkinitcpio.conf.d/ changed")
    if before.swap_exists != after.swap_exists:
        diffs.append(f"/swap existence changed: {before.swap_exists} -> {after.swap_exists}")
    if before.swap_mounted != after.swap_mounted:
        diffs.append(f"/swap mount state changed: {before.swap_mounted} -> {after.swap_mounted}")
    if before.swap_contents != after.swap_contents:
        diffs.append("/swap directory contents changed")
    if before.sleep_hooks != after.sleep_hooks:
        diffs.append("/usr/lib/systemd/system-sleep/ hooks changed")
    if before.markers != after.markers:
        diffs.append("pending rebuild markers changed")
    if parse_swaps_config(before.proc_swaps) != parse_swaps_config(after.proc_swaps):
        diffs.append(f"/proc/swaps configuration changed: {parse_swaps_config(before.proc_swaps)} -> {parse_swaps_config(after.proc_swaps)}")
    return diffs


def read_proc_swaps() -> dict[str, dict[str, int | str]]:
    swaps: dict[str, dict[str, int | str]] = {}
    if not PROC_SWAPS.exists():
        return swaps
    lines = PROC_SWAPS.read_text(encoding="utf-8").splitlines()
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 5:
            swaps[parts[0]] = {
                "type": parts[1],
                "size_kib": int(parts[2]),
                "used_kib": int(parts[3]),
                "priority": int(parts[4]),
            }
    return swaps


def detect_s2idle_active() -> bool:
    try:
        return "[s2idle]" in SYS_POWER_MEM_SLEEP.read_text(encoding="utf-8")
    except OSError:
        return False


def limine_detected() -> bool:
    return (
        LIMINE_DROPIN_DIR.is_dir()
        or shutil.which("limine-entry-tool") is not None
        or shutil.which("limine-mkinitcpio") is not None
        or shutil.which("limine-update") is not None
        or Path("/boot/limine.conf").exists()
        or Path("/boot/EFI/limine").exists()
    )


def systemd_boot_entries() -> list[Path]:
    if not SYSTEMD_BOOT_ENTRIES_DIR.is_dir():
        return []
    return sorted(p for p in SYSTEMD_BOOT_ENTRIES_DIR.glob("*.conf") if p.is_file())


def resolve_expected_uuid() -> str:
    res = subprocess.run(
        ["findmnt", "-n", "-o", "UUID", "-T", str(SWAPFILE_PATH)],
        check=True,
        text=True,
        capture_output=True,
    )
    uuid = res.stdout.strip().splitlines()[0].strip()
    expect(bool(uuid), "Failed to resolve expected UUID via findmnt.")
    return uuid


def resolve_expected_offset() -> str:
    if btrfs_bin := shutil.which("btrfs"):
        res = subprocess.run(
            [btrfs_bin, "inspect-internal", "map-swapfile", "-r", str(SWAPFILE_PATH)],
            check=False,
            text=True,
            capture_output=True,
        )
        if res.returncode == 0:
            offset = res.stdout.strip().splitlines()[-1].strip()
            if offset.isdigit() and int(offset) > 0:
                return offset

    if filefrag_bin := shutil.which("filefrag"):
        res = subprocess.run(
            [filefrag_bin, "-v", str(SWAPFILE_PATH)],
            check=True,
            text=True,
            capture_output=True,
        )
        for line in res.stdout.splitlines():
            match = re.match(r"^\s*0:\s+\d+\.\.\s*\d+:\s+(\d+)\.\.", line)
            if match:
                return match.group(1)

    raise TestFailure("Unable to independently compute resume_offset for /swap/swapfile.")


def parse_systemd_boot_option_lines(entry_path: Path) -> list[list[str]]:
    """Return tokenized `options` lines from a systemd-boot entry file."""
    result: list[list[str]] = []
    for line in entry_path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*options\s+(.*)$", line)
        if match:
            result.append(match.group(1).split())
    return result


# ---------------------------------------------------------------------------
# Subprocess Execution Helper
# ---------------------------------------------------------------------------


def run_target(
    script_path: Path,
    cli_args: Sequence[str],
    *,
    env: dict[str, str] | None = None,
    verbose: bool = False,
) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(script_path), *cli_args]
    run_env = os.environ.copy()
    run_env["NO_COLOR"] = "1"
    if env:
        run_env.update(env)

    ui.info(f"Running: {' '.join(cmd)}")
    proc = subprocess.run(
        cmd,
        check=False,
        text=True,
        capture_output=True,
        env=run_env,
    )
    if verbose or proc.returncode != 0:
        if proc.stdout.strip():
            for line in proc.stdout.splitlines():
                print(f"    {ui.dim}| {line}{ui.reset}")
        if proc.stderr.strip():
            for line in proc.stderr.splitlines():
                print(f"    {ui.red}! {line}{ui.reset}", file=sys.stderr)
    return proc


# ---------------------------------------------------------------------------
# Shared Verification Helpers
# ---------------------------------------------------------------------------


def verify_enabled_state(
    *,
    expected_size_bytes: int,
    expect_pending_marker: bool,
    had_zram_32767: bool,
) -> tuple[str, str]:
    """Verify full enabled hibernation state and return (uuid, offset)."""
    # 1. Swapfile existence, permissions, size
    expect(SWAPFILE_PATH.exists(), f"{SWAPFILE_PATH} does not exist.")
    st = SWAPFILE_PATH.stat()
    mode = stat.S_IMODE(st.st_mode)
    expect(mode == 0o600, f"Expected {SWAPFILE_PATH} mode 0o600, got {oct(mode)}.")
    size_delta = abs(st.st_size - expected_size_bytes)
    expect(
        size_delta <= 16 * 1024**2,
        f"Expected swapfile size ~{expected_size_bytes} B, got {st.st_size} B.",
    )
    ui.pass_msg(f"Swapfile verified: mode=0o600, size={st.st_size / GIB:.2f} GiB")

    # 2. Active /proc/swaps priority hierarchy
    swaps = read_proc_swaps()
    expect(str(SWAPFILE_PATH) in swaps, f"{SWAPFILE_PATH} is not active in /proc/swaps.")
    swap_info = swaps[str(SWAPFILE_PATH)]
    expect(
        swap_info["priority"] == 0,
        f"Expected {SWAPFILE_PATH} priority 0, got {swap_info['priority']}.",
    )
    active_bytes = int(swap_info["size_kib"]) * 1024
    # Kernel subtracts 1 header page (4 KiB) in /proc/swaps
    expect(
        abs(active_bytes - expected_size_bytes) <= 16 * 1024**2,
        f"/proc/swaps size mismatch: {active_bytes} B vs expected ~{expected_size_bytes} B.",
    )
    if had_zram_32767:
        expect("/dev/zram0" in swaps, "/dev/zram0 disappeared from /proc/swaps.")
        expect(
            swaps["/dev/zram0"]["priority"] == 32767,
            f"Expected /dev/zram0 priority 32767, got {swaps['/dev/zram0']['priority']}.",
        )
        ui.pass_msg("Swap hierarchy verified: /dev/zram0 (pri=32767), /swap/swapfile (pri=0)")
    else:
        ui.pass_msg("Active swap verified: /swap/swapfile (pri=0)")

    # 3. /etc/fstab single clean entry
    fstab_lines = FSTAB_PATH.read_text(encoding="utf-8").splitlines()
    swapfile_lines = [
        ln for ln in fstab_lines if ln.strip() and ln.strip().split()[0] == str(SWAPFILE_PATH)
    ]
    comment_lines = [ln for ln in fstab_lines if ln.strip() == FSTAB_COMMENT]
    expect(
        len(swapfile_lines) == 1,
        f"Expected exactly 1 {SWAPFILE_PATH} entry in /etc/fstab, found {len(swapfile_lines)}.",
    )
    expect(
        len(comment_lines) == 1,
        f"Expected exactly 1 hibernation comment in /etc/fstab, found {len(comment_lines)}.",
    )
    fields = swapfile_lines[0].split()
    expect(
        len(fields) >= 4 and fields[2] == "swap" and "pri=0" in fields[3].split(","),
        f"Unexpected fstab swap entry format: '{swapfile_lines[0]}'",
    )
    ui.pass_msg("/etc/fstab verified: single pri=0 hibernation swapfile entry")

    # 4. mkinitcpio drop-in
    expect(
        MKINITCPIO_HIBERNATION_CONF.exists(),
        f"Missing initramfs drop-in: {MKINITCPIO_HIBERNATION_CONF}",
    )
    mk_lines = [
        ln.strip()
        for ln in MKINITCPIO_HIBERNATION_CONF.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    expect(
        mk_lines == ["HOOKS+=(resume)"],
        f"Expected exact HOOKS+=(resume) in {MKINITCPIO_HIBERNATION_CONF}, got {mk_lines}.",
    )
    ui.pass_msg(f"Initramfs hook verified: {MKINITCPIO_HIBERNATION_CONF}")

    # 5. Bootloader kernel parameters (UUID + physical resume_offset + s2idle)
    expected_uuid = resolve_expected_uuid()
    expected_offset = resolve_expected_offset()
    s2idle = detect_s2idle_active()
    checked_any_bootloader = False

    sd_entries = systemd_boot_entries()
    if sd_entries:
        checked_any_bootloader = True
        for entry in sd_entries:
            opt_lines = parse_systemd_boot_option_lines(entry)
            expect(bool(opt_lines), f"No 'options' line found in {entry}")
            for tokens in opt_lines:
                resume_toks = [t for t in tokens if t.startswith("resume=")]
                offset_toks = [t for t in tokens if t.startswith("resume_offset=")]
                rtc_toks = [t for t in tokens if t.startswith("rtc_cmos.use_acpi_alarm=")]
                expect(
                    resume_toks == [f"resume=UUID={expected_uuid}"],
                    f"{entry}: expected single resume=UUID={expected_uuid}, got {resume_toks}",
                )
                expect(
                    offset_toks == [f"resume_offset={expected_offset}"],
                    f"{entry}: expected single resume_offset={expected_offset}, got {offset_toks}",
                )
                if s2idle:
                    expect(
                        rtc_toks == ["rtc_cmos.use_acpi_alarm=1"],
                        f"{entry}: s2idle active, expected rtc_cmos.use_acpi_alarm=1, got {rtc_toks}",
                    )
        ui.pass_msg(
            f"systemd-boot verified ({len(sd_entries)} entries): "
            f"resume=UUID={expected_uuid} resume_offset={expected_offset}"
        )

    if limine_detected():
        checked_any_bootloader = True
        expect(LIMINE_RESUME_CONF.exists(), f"Missing Limine drop-in: {LIMINE_RESUME_CONF}")
        limine_text = LIMINE_RESUME_CONF.read_text(encoding="utf-8")
        expect(
            f"resume=UUID={expected_uuid}" in limine_text
            and f"resume_offset={expected_offset}" in limine_text,
            f"Limine drop-in missing expected UUID/offset:\n{limine_text}",
        )
        if s2idle:
            expect(LIMINE_RTC_CONF.exists(), f"Missing Limine s2idle drop-in: {LIMINE_RTC_CONF}")
            expect(
                "rtc_cmos.use_acpi_alarm=1" in LIMINE_RTC_CONF.read_text(encoding="utf-8"),
                f"Limine rtc-alarm drop-in missing rtc_cmos.use_acpi_alarm=1",
            )
        ui.pass_msg("Limine drop-ins verified")

    if not checked_any_bootloader:
        ui.warn("Neither systemd-boot entries nor Limine detected on this VM.")

    # 6. Sleep hooks
    for hook in (SLEEP_HOOK_KBD, SLEEP_HOOK_FPRINTD):
        expect(hook.exists(), f"Missing sleep hook: {hook}")
        hmode = stat.S_IMODE(hook.stat().st_mode)
        expect(hmode == 0o755, f"Expected sleep hook {hook} mode 0o755, got {oct(hmode)}.")
    ui.pass_msg("Systemd sleep hooks verified (0o755)")

    # 7. Rebuild marker
    if expect_pending_marker:
        expect(
            DUSKY_REBUILD_MARKER.exists(),
            f"Expected pending rebuild marker {DUSKY_REBUILD_MARKER} to exist.",
        )
        ui.pass_msg(f"Pending rebuild marker verified: {DUSKY_REBUILD_MARKER}")
    else:
        expect(
            not DUSKY_REBUILD_MARKER.exists(),
            f"Expected pending rebuild marker {DUSKY_REBUILD_MARKER} to be cleared.",
        )
        ui.pass_msg("Pending rebuild marker confirmed absent")

    return expected_uuid, expected_offset


def verify_disabled_state(*, swap_was_mountpoint: bool) -> None:
    """Verify full clean teardown of all hibernation artifacts."""
    # 1. Swap off & unlinked
    swaps = read_proc_swaps()
    expect(str(SWAPFILE_PATH) not in swaps, f"{SWAPFILE_PATH} is still active in /proc/swaps.")
    expect(not SWAPFILE_PATH.exists(), f"{SWAPFILE_PATH} still exists after disable.")
    ui.pass_msg("Swapfile deactivated and unlinked")

    # 2. Mounted /swap subvolume preservation
    if swap_was_mountpoint:
        expect(SWAP_SUBVOL.exists(), "/swap mountpoint directory disappeared!")
        expect(is_mountpoint(SWAP_SUBVOL), "/swap is no longer mounted!")
        ui.pass_msg("Mounted /swap subvolume (/@swap) preserved intact")
    else:
        ui.pass_msg("Unmounted /swap subvolume handled cleanly")

    # 3. /etc/fstab cleaned
    fstab_text = FSTAB_PATH.read_text(encoding="utf-8")
    for line in fstab_text.splitlines():
        stripped = line.strip()
        expect(
            stripped != FSTAB_COMMENT,
            f"Stale hibernation comment remains in /etc/fstab: '{line}'",
        )
        fields = stripped.split()
        expect(
            not (fields and fields[0] == str(SWAPFILE_PATH)),
            f"Stale swapfile entry remains in /etc/fstab: '{line}'",
        )
    ui.pass_msg("/etc/fstab cleaned of hibernation entries")

    # 4. mkinitcpio drop-ins removed
    for dropin in (MKINITCPIO_HIBERNATION_CONF, MKINITCPIO_OMARCHY_CONF):
        expect(not dropin.exists(), f"Initramfs drop-in still exists: {dropin}")
    ui.pass_msg("Initramfs drop-ins removed")

    # 5. Bootloader params cleaned
    for entry in systemd_boot_entries():
        for tokens in parse_systemd_boot_option_lines(entry):
            leftovers = [
                t
                for t in tokens
                if t.startswith(("resume=", "resume_offset=", "rtc_cmos.use_acpi_alarm="))
            ]
            expect(
                not leftovers,
                f"Stale hibernation kernel params remain in {entry}: {leftovers}",
            )
    for dropin in (LIMINE_RESUME_CONF, LIMINE_RTC_CONF):
        expect(not dropin.exists(), f"Limine drop-in still exists: {dropin}")
    ui.pass_msg("Bootloader resume parameters cleaned")

    # 6. Sleep hooks removed
    for hook in (SLEEP_HOOK_KBD, SLEEP_HOOK_FPRINTD):
        expect(not hook.exists(), f"Sleep hook still exists: {hook}")
    ui.pass_msg("Sleep hooks removed")

    # 7. Rebuild markers removed
    for marker in (DUSKY_REBUILD_MARKER, OMARCHY_REBUILD_MARKER):
        expect(not marker.exists(), f"Rebuild marker still exists: {marker}")
    ui.pass_msg("Pending rebuild markers cleaned")


# ---------------------------------------------------------------------------
# Test Context & 7 Verification Phases
# ---------------------------------------------------------------------------


@dataclass
class TestContext:
    script_path: Path
    verbose: bool
    swap_is_mountpoint: bool
    had_zram_32767: bool
    phase2_offset: str = ""
    phase2_uuid: str = ""


def phase_1_invariance(ctx: TestContext) -> None:
    """Phase 1: Read-Only / Dry-Run Invariance."""
    before = capture_snapshot()

    for flag_set in (
        ["--check"],
        ["--status"],
        ["--enable", "--dry-run", "--no-rebuild", "--size", "1G"],
        ["--enable", "-n", "--size", "1G"],
        ["--disable", "--dry-run", "--no-rebuild"],
    ):
        proc = run_target(ctx.script_path, flag_set, verbose=ctx.verbose)
        expect(
            proc.returncode == 0,
            f"Command {' '.join(flag_set)} failed with exit code {proc.returncode}.",
        )
        after = capture_snapshot()
        diffs = diff_snapshots(before, after)
        expect(
            not diffs,
            f"State mutated during read-only/dry-run {' '.join(flag_set)}: {'; '.join(diffs)}",
        )
        ui.pass_msg(f"Zero mutations verified for: {' '.join(flag_set)}")


def phase_2_first_enable(ctx: TestContext) -> None:
    """Phase 2: First-Time Enable & Provisioning (--enable --no-rebuild --size 1G)."""
    # Normalize to clean state if VM already had an older swapfile before test run
    if SWAPFILE_PATH.exists() or MKINITCPIO_HIBERNATION_CONF.exists():
        ui.warn("Pre-existing hibernation artifacts detected; normalizing via --disable --no-rebuild first.")
        pre_clean = run_target(ctx.script_path, ["--disable", "--no-rebuild"], verbose=ctx.verbose)
        expect(pre_clean.returncode == 0, "Pre-test normalization (--disable) failed.")

    proc = run_target(
        ctx.script_path,
        ["--enable", "--no-rebuild", "--size", "1G"],
        verbose=ctx.verbose,
    )
    expect(proc.returncode == 0, f"--enable --no-rebuild --size 1G exited {proc.returncode}.")

    uuid, offset = verify_enabled_state(
        expected_size_bytes=1 * GIB,
        expect_pending_marker=True,
        had_zram_32767=ctx.had_zram_32767,
    )
    ctx.phase2_uuid = uuid
    ctx.phase2_offset = offset


def phase_3_idempotency(ctx: TestContext) -> None:
    """Phase 3: Idempotency Under Duplication (second --enable --no-rebuild --size 1G)."""
    before_fstab = FSTAB_PATH.read_text(encoding="utf-8")
    before_mk = MKINITCPIO_HIBERNATION_CONF.read_text(encoding="utf-8")
    before_ino = SWAPFILE_PATH.stat().st_ino

    proc = run_target(
        ctx.script_path,
        ["--enable", "--no-rebuild", "--size", "1G"],
        verbose=ctx.verbose,
    )
    expect(proc.returncode == 0, f"Second --enable exited {proc.returncode}.")

    uuid, offset = verify_enabled_state(
        expected_size_bytes=1 * GIB,
        expect_pending_marker=True,
        had_zram_32767=ctx.had_zram_32767,
    )
    expect(
        FSTAB_PATH.read_text(encoding="utf-8") == before_fstab,
        "/etc/fstab changed on idempotent re-run.",
    )
    expect(
        MKINITCPIO_HIBERNATION_CONF.read_text(encoding="utf-8") == before_mk,
        "15-hibernation.conf changed on idempotent re-run.",
    )
    expect(
        SWAPFILE_PATH.stat().st_ino == before_ino,
        "Swapfile was unnecessarily recreated (inode changed) on identical size re-run.",
    )
    expect(uuid == ctx.phase2_uuid and offset == ctx.phase2_offset, "UUID/offset drifted on idempotent re-run.")
    ui.pass_msg("Idempotency verified: zero duplicate lines, swapfile preserved in-place")


def phase_4_dynamic_resize(ctx: TestContext) -> None:
    """Phase 4: Dynamic Resizing (--enable --no-rebuild --size 2G)."""
    prev_ino = SWAPFILE_PATH.stat().st_ino
    prev_size = SWAPFILE_PATH.stat().st_size

    proc = run_target(
        ctx.script_path,
        ["--enable", "--no-rebuild", "--size", "2G"],
        verbose=ctx.verbose,
    )
    expect(proc.returncode == 0, f"Resize to 2G exited {proc.returncode}.")

    new_uuid, new_offset = verify_enabled_state(
        expected_size_bytes=2 * GIB,
        expect_pending_marker=True,
        had_zram_32767=ctx.had_zram_32767,
    )
    new_size = SWAPFILE_PATH.stat().st_size
    expect(
        new_size > prev_size + (512 * 1024**2),
        f"Swapfile did not grow as expected: {prev_size} -> {new_size}.",
    )
    ui.info(
        f"Resize transition: inode {prev_ino} -> {SWAPFILE_PATH.stat().st_ino}, "
        f"offset {ctx.phase2_offset} -> {new_offset}, UUID={new_uuid}"
    )
    ui.pass_msg("Dynamic resize to 2 GiB and bootloader offset refresh verified")


def phase_5_disable_teardown(ctx: TestContext) -> None:
    """Phase 5: Disable & Teardown (--disable --no-rebuild)."""
    # Seed legacy omarchy artifacts transiently to verify complete cleanup coverage
    created_omarchy_dir = False
    if not OMARCHY_REBUILD_MARKER.parent.exists():
        OMARCHY_REBUILD_MARKER.parent.mkdir(parents=True, exist_ok=True)
        created_omarchy_dir = True
    OMARCHY_REBUILD_MARKER.write_text("pending=1\n", encoding="utf-8")
    MKINITCPIO_OMARCHY_CONF.write_text("HOOKS+=(resume)\n", encoding="utf-8")

    try:
        proc = run_target(ctx.script_path, ["--disable", "--no-rebuild"], verbose=ctx.verbose)
        expect(proc.returncode == 0, f"--disable --no-rebuild exited {proc.returncode}.")
        verify_disabled_state(swap_was_mountpoint=ctx.swap_is_mountpoint)
    finally:
        if created_omarchy_dir and OMARCHY_REBUILD_MARKER.parent.exists():
            try:
                OMARCHY_REBUILD_MARKER.parent.rmdir()
            except OSError:
                pass


def phase_6_teardown_idempotency(ctx: TestContext) -> None:
    """Phase 6: Teardown Idempotency (second --disable --no-rebuild on clean system)."""
    before = capture_snapshot()
    proc = run_target(ctx.script_path, ["--disable", "--no-rebuild"], verbose=ctx.verbose)
    expect(proc.returncode == 0, f"Second --disable exited {proc.returncode}.")
    expect("Traceback" not in proc.stderr, f"Unhandled traceback in stderr:\n{proc.stderr}")

    verify_disabled_state(swap_was_mountpoint=ctx.swap_is_mountpoint)
    after = capture_snapshot()
    diffs = diff_snapshots(before, after)
    expect(
        not diffs,
        f"Clean system mutated on idempotent --disable: {'; '.join(diffs)}",
    )
    ui.pass_msg("Teardown idempotency verified: exit 0, zero errors, zero mutations")


def phase_7_rebuild_simulation(ctx: TestContext) -> None:
    """
    Phase 7: Standalone Rebuild Simulation.
    Tests default standalone rebuild vs --no-rebuild / --no-initramfs / --no-init-gen
    using a deterministic temporary PATH shim for mkinitcpio / limine-mkinitcpio so
    real /boot initramfs images are not overwritten unnecessarily during testing.
    """
    # 7a. Dry-run CLI flag parity across --no-rebuild aliases vs default rebuild
    proc_default_dry = run_target(
        ctx.script_path, ["--enable", "--dry-run", "--size", "1G"], verbose=ctx.verbose
    )
    expect(proc_default_dry.returncode == 0, "Dry-run default rebuild failed.")
    expect(
        ("mkinitcpio" in proc_default_dry.stdout or "limine-mkinitcpio" in proc_default_dry.stdout)
        and "--no-rebuild" not in proc_default_dry.stdout,
        f"Default dry-run did not plan an initramfs rebuild:\n{proc_default_dry.stdout}",
    )
    ui.pass_msg("Dry-run default mode correctly schedules initramfs rebuild")

    for alias in ("--no-rebuild", "--no-initramfs", "--no-init-gen"):
        proc_alias = run_target(
            ctx.script_path, ["--enable", "--dry-run", alias, "--size", "1G"], verbose=ctx.verbose
        )
        expect(proc_alias.returncode == 0, f"Dry-run with {alias} failed.")
        expect(
            "Skipped initramfs rebuild" in proc_alias.stdout
            and str(DUSKY_REBUILD_MARKER) in proc_alias.stdout,
            f"Dry-run with {alias} did not skip rebuild & plan marker:\n{proc_alias.stdout}",
        )
        ui.pass_msg(f"Alias {alias} verified to defer initramfs rebuild")

    # 7b. Live execution with shimmed initramfs tools to verify invocation & marker lifecycle
    has_limine_mk = shutil.which("limine-mkinitcpio") is not None
    has_mkinitcpio = shutil.which("mkinitcpio") is not None
    has_limine_upd = shutil.which("limine-update") is not None
    expect(
        has_limine_mk or has_mkinitcpio,
        "Host has neither mkinitcpio nor limine-mkinitcpio installed.",
    )

    with tempfile.TemporaryDirectory(prefix="dusky-hiber-shim-") as tmp_dir:
        shim_dir = Path(tmp_dir)
        log_file = shim_dir / "invocations.log"
        log_file.write_text("", encoding="utf-8")

        def create_shim(tool_name: str) -> None:
            shim_path = shim_dir / tool_name
            shim_path.write_text(
                f"#!/usr/bin/env bash\n"
                f'echo "{tool_name} $*" >> "{log_file}"\n'
                f"exit 0\n",
                encoding="utf-8",
            )
            os.chmod(shim_path, 0o755)

        if has_limine_mk:
            create_shim("limine-mkinitcpio")
        if has_mkinitcpio:
            create_shim("mkinitcpio")
        if has_limine_upd:
            create_shim("limine-update")

        shimmed_env = {"PATH": f"{shim_dir}:{os.environ.get('PATH', '')}"}

        try:
            # Step 1: Enable with --no-initramfs -> shim NOT called, marker CREATED
            proc_no_reb = run_target(
                ctx.script_path,
                ["--enable", "--no-initramfs", "--size", "1G"],
                env=shimmed_env,
                verbose=ctx.verbose,
            )
            expect(proc_no_reb.returncode == 0, "Shimmed --enable --no-initramfs failed.")
            expect(
                log_file.read_text(encoding="utf-8").strip() == "",
                f"Initramfs tool was unexpectedly invoked under --no-initramfs: {log_file.read_text()}",
            )
            expect(DUSKY_REBUILD_MARKER.exists(), "Pending marker not created under --no-initramfs.")
            ui.pass_msg("Deferred enable (--no-initramfs): 0 rebuild calls, pending marker created")

            # Step 2: Standalone enable (no --no-rebuild) -> shim CALLED, marker CLEARED
            proc_reb = run_target(
                ctx.script_path,
                ["--enable", "--size", "1G"],
                env=shimmed_env,
                verbose=ctx.verbose,
            )
            expect(proc_reb.returncode == 0, "Shimmed standalone --enable failed.")
            invocations = [
                ln.strip() for ln in log_file.read_text(encoding="utf-8").splitlines() if ln.strip()
            ]
            expected_tool = "limine-mkinitcpio" if has_limine_mk else "mkinitcpio -P"
            expect(
                any(expected_tool in line for line in invocations),
                f"Expected '{expected_tool}' invocation on standalone enable, got: {invocations}",
            )
            expect(
                not DUSKY_REBUILD_MARKER.exists(),
                "Pending rebuild marker was not cleared after standalone rebuild.",
            )
            ui.pass_msg(
                f"Standalone enable triggered '{invocations[0]}' and cleared pending marker"
            )

            # Step 3: Standalone disable (no --no-rebuild) -> shim CALLED, system cleaned
            log_file.write_text("", encoding="utf-8")
            proc_dis = run_target(
                ctx.script_path,
                ["--disable"],
                env=shimmed_env,
                verbose=ctx.verbose,
            )
            expect(proc_dis.returncode == 0, "Shimmed standalone --disable failed.")
            dis_invocations = [
                ln.strip() for ln in log_file.read_text(encoding="utf-8").splitlines() if ln.strip()
            ]
            expect(
                any(expected_tool in line for line in dis_invocations),
                f"Expected '{expected_tool}' invocation on standalone disable, got: {dis_invocations}",
            )
            verify_disabled_state(swap_was_mountpoint=ctx.swap_is_mountpoint)
            ui.pass_msg(
                f"Standalone disable triggered '{dis_invocations[0]}' and left system clean"
            )
        finally:
            # Ensure swapfile is not left behind if an assertion inside Step 1/2 failed
            if SWAPFILE_PATH.exists():
                run_target(ctx.script_path, ["--disable", "--no-rebuild"], verbose=False)


# ---------------------------------------------------------------------------
# Suite Orchestration & Reporting
# ---------------------------------------------------------------------------


@dataclass
class PhaseRecord:
    number: int
    name: str
    passed: bool
    duration_s: float
    error: str = ""


def print_vm_diagnostics(script_path: Path) -> tuple[bool, bool]:
    ui.banner("VM Environment Preflight Diagnostics")
    ui.info(f"Target script : {script_path}")
    ui.info(f"Python        : {sys.version.split()[0]}")
    ui.info(f"Kernel        : {os.uname().release}")

    power_state = SYS_POWER_STATE.read_text(encoding="utf-8").strip() if SYS_POWER_STATE.exists() else "N/A"
    lockdown = SYS_KERNEL_LOCKDOWN.read_text(encoding="utf-8").strip() if SYS_KERNEL_LOCKDOWN.exists() else "none"
    swap_mounted = is_mountpoint(SWAP_SUBVOL)
    swaps = read_proc_swaps()
    has_zram_32767 = "/dev/zram0" in swaps and swaps["/dev/zram0"]["priority"] == 32767

    ui.info(f"/sys/power/state : {power_state}")
    ui.info(f"Kernel lockdown  : {lockdown}")
    ui.info(f"/swap mountpoint : {'yes (mounted subvolume)' if swap_mounted else 'no'}")
    ui.info(f"/dev/zram0 32767 : {'yes' if has_zram_32767 else 'no'}")
    ui.info(f"systemd-boot     : {len(systemd_boot_entries())} entries")
    ui.info(f"Limine detected  : {'yes' if limine_detected() else 'no'}")
    return swap_mounted, has_zram_32767


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="test_218_hibernation.py",
        description="Stress-test & verification suite for 218_hibernation_setup.py.",
    )
    parser.add_argument(
        "--script",
        type=Path,
        default=DEFAULT_SCRIPT_PATH,
        help=f"Path to 218_hibernation_setup.py (default: {DEFAULT_SCRIPT_PATH}).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Stream full stdout/stderr from 218_hibernation_setup.py on every invocation.",
    )
    parser.add_argument(
        "--keep-backups",
        action="store_true",
        help="Retain /etc/fstab.bak.* files created during the test run.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if os.geteuid() != 0:
        ui.fail("This test suite must be executed with root privileges: sudo python3 test_218_hibernation.py")
        return 1

    script_path: Path = args.script
    if not script_path.exists():
        local_fallback = Path("./218_hibernation_setup.py").resolve()
        if local_fallback.exists():
            script_path = local_fallback
        else:
            ui.fail(f"Target script not found: {script_path}")
            return 1

    initial_backups = set(list_fstab_backups())
    swap_mounted, had_zram_32767 = print_vm_diagnostics(script_path)

    ctx = TestContext(
        script_path=script_path,
        verbose=args.verbose,
        swap_is_mountpoint=swap_mounted,
        had_zram_32767=had_zram_32767,
    )

    phases: list[tuple[int, str, Callable[[TestContext], None]]] = [
        (1, "Read-Only / Dry-Run Invariance", phase_1_invariance),
        (2, "First-Time Enable & Provisioning (1G)", phase_2_first_enable),
        (3, "Idempotency Under Duplication (1G)", phase_3_idempotency),
        (4, "Dynamic Resizing (1G -> 2G)", phase_4_dynamic_resize),
        (5, "Disable & Teardown", phase_5_disable_teardown),
        (6, "Teardown Idempotency", phase_6_teardown_idempotency),
        (7, "Standalone Rebuild Simulation", phase_7_rebuild_simulation),
    ]

    records: list[PhaseRecord] = []
    suite_start = time.monotonic()

    try:
        for num, name, fn in phases:
            ui.phase(num, name)
            t0 = time.monotonic()
            try:
                fn(ctx)
                records.append(PhaseRecord(num, name, True, time.monotonic() - t0))
            except Exception as exc:
                elapsed = time.monotonic() - t0
                ui.fail(f"Phase {num} failed: {exc}")
                records.append(PhaseRecord(num, name, False, elapsed, str(exc)))
                # Phases 2->3->4->5 depend on preceding state; abort chain if Phase 2 fails
                if num == 2:
                    ui.warn("Aborting dependent phases 3-5 because Phase 2 (initial enable) failed.")
                    break
    finally:
        if not args.keep_backups:
            created_backups = set(list_fstab_backups()) - initial_backups
            for bak_name in sorted(created_backups):
                bak_path = Path("/etc") / bak_name
                try:
                    bak_path.unlink(missing_ok=True)
                except OSError:
                    pass
            if created_backups:
                ui.info(f"Cleaned up {len(created_backups)} test-generated /etc/fstab.bak.* file(s).")

    total_time = time.monotonic() - suite_start
    passed_count = sum(1 for r in records if r.passed)
    failed_count = len(records) - passed_count

    ui.banner("Test Suite Summary")
    for rec in records:
        status = f"{ui.green}PASS{ui.reset}" if rec.passed else f"{ui.red}FAIL{ui.reset}"
        print(f"  Phase {rec.number}: [{status}] {rec.name:<42} ({rec.duration_s:.2f}s)")
        if rec.error:
            print(f"           {ui.red}-> {rec.error}{ui.reset}")

    print(
        f"\n  Total: {passed_count}/{len(records)} passed, "
        f"{failed_count} failed in {total_time:.2f}s."
    )
    return 0 if failed_count == 0 and len(records) == len(phases) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nTest suite interrupted by user.", file=sys.stderr)
        raise SystemExit(130)
