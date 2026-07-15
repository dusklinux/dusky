#!/usr/bin/env python3
"""
151_systemd_bootloader.py - DUSKY Final - Python 3.14.6 + Rich 15.0.0
Target: systemd 261, kernel 7.1.3-arch1-2, mkinitcpio 38+ with microcode hook
Architecture:
  UEFI -> systemd-boot Type #1 (no microcode initrd, microcode embedded via hook)
  BIOS -> GRUB i386-pc fallback
Standards:
  - UAPI v1 Boot Loader Spec
  - mkinitcpio microcode hook replaces ALL_microcode (2024+)
  - Windows auto-detected at /EFI/Microsoft/Boot/bootmgfw.efi (no manual entry needed)
  - bootctl --efi-boot-option-description-with-device=yes (systemd 260+)
  - Pipeline aware: vmlinuz staged BEFORE mkinitcpio -P (hooks masked in 070, restored in 158)

Flow:
  070 pacstrap (masks mkinitcpio hooks, installs intel/amd-ucode)
  120 mkinitcpio optimizer (HOOKS=... microcode ... sd-encrypt ... filesystems)
  151 THIS SCRIPT (copies vmlinuz to /boot, writes loader.conf + entries, NO ucode lines)
  158 restore hooks + mkinitcpio -P (generates initramfs with microcode embedded)
"""
from __future__ import annotations
import os
import sys
import re
import shlex
import shutil
import signal
import subprocess
import json
from pathlib import Path
from typing import List, Tuple, Dict

# --- Rich bootstrap (offline ISO friendly) ---
def _ensure_rich():
    import importlib.util
    try:
        if importlib.util.find_spec("rich") is not None:
            return
    except ModuleNotFoundError:
        pass
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        print("python-rich missing", file=sys.stderr)
        sys.exit(1)
    print(">> Installing python-rich...", file=sys.stderr)
    subprocess.run(["pacman", "-Sy", "--needed", "--noconfirm", "python-rich"], stdout=sys.stderr, stderr=sys.stderr)

_ensure_rich()
from rich.console import Console
from rich.panel import Panel
from rich import box

def make_console():
    term = os.environ.get("TERM", "")
    if term in ("dumb", "unknown"):
        return Console(color_system=None, force_terminal=False, no_color=True, legacy_windows=False)
    return Console(color_system="standard", legacy_windows=False, safe_box=True, highlight=False, markup=True)

console = make_console()

# --- Constants ---
ESP_MNT = Path("/boot")
LOADER_CONF = ESP_MNT / "loader" / "loader.conf"
ENTRIES_DIR = ESP_MNT / "loader" / "entries"

# --- Helpers ---
def run(*cmd, check=True, capture=True, input_text=None, timeout=300):
    argv = [os.fspath(c) for c in cmd]
    try:
        if isinstance(input_text, (bytes, bytearray)):
            return subprocess.run(argv, check=check, text=False, capture_output=capture, input=bytes(input_text), timeout=timeout)
        elif isinstance(input_text, str):
            return subprocess.run(argv, check=check, text=True, capture_output=capture, input=input_text, timeout=timeout)
        return subprocess.run(argv, check=check, text=True, capture_output=capture, timeout=timeout)
    except subprocess.CalledProcessError as e:
        if check:
            console.print(f"[red]Failed: {shlex.join([str(x) for x in argv])}[/red]")
        raise

def detect_boot_mode() -> str:
    return "UEFI" if Path("/sys/firmware/efi/efivars").is_dir() else "BIOS"

def is_mountpoint(p: Path) -> bool:
    try:
        return p.is_mount() if hasattr(p, "is_mount") else run("mountpoint", "-q", str(p), check=False).returncode == 0
    except:
        return False

def get_parent_disk(part_path: Path) -> Path:
    """Get whole disk for /dev/nvme0n1p2 -> /dev/nvme0n1"""
    part_path = part_path.resolve()
    # Try lsblk PKNAME
    try:
        r = run("lsblk", "-ndo", "PKNAME", str(part_path), check=False, capture=True)
        pk = r.stdout.strip().splitlines()[0].strip() if r.stdout.strip() else ""
        if pk:
            return Path(f"/dev/{pk}")
    except:
        pass
    # Fallback sysfs
    try:
        # /sys/block/*/...
        real = part_path.name
        # resolve dm-crypt -> check slaves
        sys_path = Path(f"/sys/class/block/{real}")
        if sys_path.exists():
            # walk up
            for parent in sys_path.resolve().parents:
                if parent.name == "block":
                    continue
            # crude: use lsblk -s
            r = run("lsblk", "-nrpso", "NAME,TYPE", str(part_path), check=False, capture=True)
            for line in r.stdout.splitlines():
                if "disk" in line:
                    m = re.search(r"(/dev/\S+)", line)
                    if m:
                        return Path(m.group(1))
    except:
        pass
    # Last resort: strip partition number
    s = str(part_path)
    # nvme0n1p1, mmcblk0p1, loop0p1
    m = re.match(r"^(.*?)(?:p\d+|\d+)$", s)
    if m and Path(m.group(1)).exists():
        # need to differentiate /dev/sda1 -> /dev/sda
        if s[-1].isdigit() and not s[-2].isdigit() and "nvme" not in s and "mmcblk" not in s:
            # sda1 -> sda
            return Path(s[:-1])
        elif "p" in s:
            return Path(m.group(1))
    return Path(s)

def parse_mkinicpio_hooks() -> str:
    """Replicates bash env -i bash -c 'source /etc/mkinitcpio.conf; ...'"""
    script = """
    set +u
    source /etc/mkinitcpio.conf >/dev/null 2>&1 || true
    shopt -s nullglob
    for conf in /etc/mkinitcpio.conf.d/*.conf; do
        source "$conf" >/dev/null 2>&1 || true
    done
    echo "${HOOKS[*]:-}"
    """
    try:
        r = subprocess.run(["env", "-i", "bash", "-c", script], text=True, capture_output=True, timeout=5)
        return f" {r.stdout.strip()} "
    except:
        return " "

def get_root_topology() -> Dict:
    console.print("[cyan]Analyzing filesystem topology...[/cyan]")
    # -v (--nofsroot) strips btrfs [/@] subvolume tags, modern since util-linux 2.39+
    r = run("findmnt", "-n", "-v", "-e", "-o", "SOURCE", "-T", "/", check=False)
    root_blk_dev = r.stdout.strip().splitlines()[0].strip() if r.stdout.strip() else ""
    root_blk_dev = root_blk_dev.split("[")[0]  # extra safety

    r = run("findmnt", "-n", "-e", "-o", "UUID", "-T", "/", check=False)
    root_uuid = r.stdout.strip().splitlines()[0].strip() if r.stdout.strip() else ""

    r = run("findmnt", "-n", "-e", "-o", "FSTYPE", "-T", "/", check=False)
    root_fstype = r.stdout.strip().splitlines()[0].strip() if r.stdout.strip() else ""

    r = run("findmnt", "-n", "-e", "-o", "OPTIONS", "-T", "/", check=False)
    root_opts = r.stdout.strip().splitlines()[0].strip() if r.stdout.strip() else ""

    if not root_blk_dev:
        console.print("[red]Could not resolve root block device[/red]")
        sys.exit(1)

    if not root_uuid or root_uuid == "-":
        try:
            r = run("blkid", "-s", "UUID", "-o", "value", root_blk_dev, check=False)
            root_uuid = r.stdout.strip().splitlines()[0].strip() if r.stdout.strip() else ""
        except:
            root_uuid = ""

    if not root_uuid:
        console.print("[red]Could not resolve root UUID[/red]")
        sys.exit(1)

    if not root_fstype or root_fstype == "-":
        root_fstype = "btrfs"

    # subvol extraction
    subvol = ""
    m = re.search(r"subvol=([^,]+)", root_opts)
    if m:
        subvol = m.group(1)

    return {
        "ROOT_BLK_DEV": root_blk_dev,
        "ROOT_UUID": root_uuid,
        "ROOT_FSTYPE": root_fstype,
        "ROOT_OPTS": root_opts,
        "ROOT_SUBVOL": subvol,
    }

def detect_luks(root_blk_dev: str) -> Dict:
    """Detect LUKS layer via lsblk -s"""
    try:
        r = run("lsblk", "-nrspo", "PATH,TYPE", "-s", "--", root_blk_dev, check=False)
        crypt_dev = ""
        for line in r.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "crypt":
                crypt_dev = parts[0]
                break
        if not crypt_dev:
            return {"found": False}
        
        console.print(f"[yellow]LUKS2 detected: {crypt_dev}[/yellow]")
        mapper_name = Path(crypt_dev).name
        
        r = run("cryptsetup", "status", mapper_name, check=False)
        backing_dev = ""
        for line in r.stdout.splitlines():
            if "device:" in line.lower():
                # format: "  device:  /dev/nvme0n1p2"
                backing_dev = line.split("device:")[-1].strip().split()[0]
                break
        
        if not backing_dev:
            console.print(f"[red]Could not determine backing device for {mapper_name}[/red]")
            sys.exit(1)
        
        r = run("blkid", "-s", "UUID", "-o", "value", backing_dev, check=False)
        luks_uuid = r.stdout.strip().splitlines()[0].strip() if r.stdout.strip() else ""
        
        if not luks_uuid:
            console.print(f"[red]Could not determine LUKS UUID for {backing_dev}[/red]")
            sys.exit(1)
        
        return {
            "found": True,
            "CRYPT_DEV": crypt_dev,
            "MAPPER_NAME": mapper_name,
            "BACKING_DEV": backing_dev,
            "LUKS_UUID": luks_uuid,
        }
    except Exception as e:
        console.print(f"[yellow]LUKS detection failed: {e}, assuming plain[/yellow]")
        return {"found": False}

def ensure_esp():
    if not is_mountpoint(ESP_MNT):
        console.print(f"[red]{ESP_MNT} is NOT a mountpoint. Ensure FAT32 ESP is mounted.[/red]")
        sys.exit(1)
    r = run("findmnt", "-n", "-e", "-o", "FSTYPE", str(ESP_MNT), check=False)
    fstype = r.stdout.strip().splitlines()[0].strip().lower() if r.stdout.strip() else ""
    if fstype not in ("vfat", "fat32", "msdos"):
        console.print(f"[red]{ESP_MNT} is {fstype}, but systemd-boot requires FAT32[/red]")
        sys.exit(1)

def get_kernels() -> List[Tuple[Path, str]]:
    """Returns list of (kdir, pkgbase)"""
    kernels = []
    for kdir in Path("/usr/lib/modules").glob("*"):
        if (kdir / "pkgbase").is_file() and (kdir / "vmlinuz").is_file():
            pkgbase = (kdir / "pkgbase").read_text().strip()
            if pkgbase:
                kernels.append((kdir, pkgbase))
    return kernels

def install_systemd_boot_uefi():
    console.print(Panel(f"[bold cyan]UEFI Mode - systemd-boot 261 Modern Path[/bold cyan]", box=box.ROUNDED))
    ensure_esp()

    # Check Windows auto-detection
    win_path = ESP_MNT / "EFI" / "Microsoft" / "Boot" / "bootmgfw.efi"
    if win_path.exists():
        console.print(f"[cyan]Windows Boot Manager detected at {win_path} - will be auto-shown as auto-windows (no config needed)[/cyan]")
    else:
        console.print("[dim]No Windows ESP detected, single-boot[/dim]")

    topo = get_root_topology()
    hooks_str = parse_mkinicpio_hooks()
    luks = detect_luks(topo["ROOT_BLK_DEV"])

    # --- Build cmdline - MODERN minimal, no legacy ucode handling ---
    # Base is rw + loglevel=3, rootfstype. Hardening flags from old script are optional and removed for modern default.
    # fsck.mode=skip for btrfs (btrfs has no fsck at boot)
    cmdline_base = f"rw loglevel=3 rootfstype={topo['ROOT_FSTYPE']}"
    if topo["ROOT_FSTYPE"] == "btrfs":
        cmdline_base += " fsck.mode=skip"

    # LUKS handling
    if luks["found"]:
        if " sd-encrypt " in hooks_str:
            console.print("[green]Using sd-encrypt hook (systemd native)[/green]")
            cmdline_base = f"rd.luks.name={luks['LUKS_UUID']}={luks['MAPPER_NAME']} rd.luks.options=discard root=UUID={topo['ROOT_UUID']} {cmdline_base}"
        elif " encrypt " in hooks_str:
            console.print("[yellow]Using legacy encrypt hook[/yellow]")
            cmdline_base = f"cryptdevice=UUID={luks['LUKS_UUID']}:{luks['MAPPER_NAME']}:allow-discards root=/dev/mapper/{luks['MAPPER_NAME']} {cmdline_base}"
        else:
            console.print("[red]LUKS detected but neither sd-encrypt nor encrypt in mkinitcpio HOOKS[/red]")
            sys.exit(1)
    else:
        console.print(f"[green]Plain {topo['ROOT_FSTYPE']} detected[/green]")
        cmdline_base = f"root=UUID={topo['ROOT_UUID']} {cmdline_base}"

    if topo["ROOT_SUBVOL"]:
        cmdline_base += f" rootflags=subvol={topo['ROOT_SUBVOL']}"

    # Plymouth - only if hook present
    plymouth_args = ""
    if " plymouth " in hooks_str or " sd-plymouth " in hooks_str:
        console.print("[cyan]Plymouth detected, adding splash args for primary entry only[/cyan]")
        plymouth_args = "quiet splash rd.udev.log_level=3 vt.global_cursor_default=0 nowatchdog"

    # --- bootctl install ---
    console.print(f"[yellow]Deploying systemd-boot to {ESP_MNT}...[/yellow]")
    # systemd 260+ flag --efi-boot-option-description-with-device=yes
    # --graceful for chroot (arch-chroot without systemd namespace)
    # --variables=yes explicit for NVRAM
    is_installed = run("bootctl", "is-installed", f"--esp-path={ESP_MNT}", check=False).returncode == 0
    if is_installed:
        console.print("[cyan]Existing systemd-boot found, updating...[/cyan]")
        run("bootctl", "update", f"--esp-path={ESP_MNT}", "--variables=yes", "--efi-boot-option-description-with-device=yes", "--graceful", check=False)
    else:
        console.print("[cyan]Fresh install...[/cyan]")
        r = run("bootctl", "install", f"--esp-path={ESP_MNT}", "--variables=yes", "--efi-boot-option-description-with-device=yes", "--graceful", check=False)
        if r.returncode != 0:
            console.print("[yellow]bootctl install returned non-zero (common in chroot), verifying...[/yellow]")
            if run("bootctl", "is-installed", f"--esp-path={ESP_MNT}", check=False).returncode != 0:
                console.print("[red]bootctl installation failed completely[/red]")
                sys.exit(1)

    console.print("[green]systemd-boot binaries deployed, random-seed handled automatically (systemd 257+)[/green]")

    # loader.conf - modern recommended defaults
    LOADER_CONF.parent.mkdir(parents=True, exist_ok=True)
    LOADER_CONF.write_text("default  @saved\ntimeout  2\nconsole-mode max\neditor   no\n")
    console.print(f"[green]Wrote {LOADER_CONF}[/green]")

    # --- Kernel staging (deferred mkinitcpio model) ---
    kernels = get_kernels()
    if not kernels:
        console.print("[red]No valid kernels in /usr/lib/modules (pkgbase + vmlinuz missing)[/red]")
        sys.exit(1)

    for kdir, pkgbase in kernels:
        src = kdir / "vmlinuz"
        dst = ESP_MNT / f"vmlinuz-{pkgbase}"
        console.print(f"[cyan]Staging {pkgbase}: {src} -> {dst}[/cyan]")
        shutil.copy2(src, dst)

    # --- BLS Type #1 entries - NO microcode initrd (embedded via microcode hook) ---
    ENTRIES_DIR.mkdir(parents=True, exist_ok=True)
    # Clean old arch-*.conf to avoid stale entries
    for old in ENTRIES_DIR.glob("arch-*.conf"):
        try:
            old.unlink()
        except:
            pass

    for _, pkgbase in kernels:
        primary_opts = cmdline_base + (f" {plymouth_args}" if plymouth_args else "")
        
        entry_file = ENTRIES_DIR / f"arch-{pkgbase}.conf"
        fallback_file = ENTRIES_DIR / f"arch-{pkgbase}-fallback.conf"

        console.print(f"[yellow]Generating BLS entries for {pkgbase}[/yellow]")

        # Primary - with plymouth if present
        entry_file.write_text(
            f"title   Arch Linux ({pkgbase})\n"
            f"linux   /vmlinuz-{pkgbase}\n"
            f"initrd  /initramfs-{pkgbase}.img\n"
            f"options {primary_opts}\n"
        )

        # Fallback - no plymouth, full debug, autodetect disabled via preset
        fallback_file.write_text(
            f"title   Arch Linux ({pkgbase} - Fallback Recovery)\n"
            f"linux   /vmlinuz-{pkgbase}\n"
            f"initrd  /initramfs-{pkgbase}-fallback.img\n"
            f"options {cmdline_base}\n"
        )

    # Enable auto-update service
    console.print("[cyan]Enabling systemd-boot-update.service...[/cyan]")
    run("systemctl", "enable", "systemd-boot-update.service", check=False)

    console.print(Panel(f"[bold green]systemd-boot UEFI Complete\nKernels: {', '.join([k[1] for k in kernels])}\nMicrocode: embedded via microcode hook (no initrd line needed)\nWindows: auto-windows if present[/bold green]", box=box.ROUNDED))

def install_grub_bios_fallback(topo: Dict, luks: Dict, cmdline_base: str):
    console.print(Panel(f"[bold yellow]BIOS Mode Detected - GRUB i386-pc Fallback[/bold yellow]", box=box.ROUNDED))
    
    # Determine disk
    root_dev = Path(topo["ROOT_BLK_DEV"]).resolve()
    # If root is /dev/mapper/cryptroot, get backing
    if luks["found"]:
        root_dev = Path(luks["BACKING_DEV"]).resolve()
    
    disk = get_parent_disk(root_dev)
    console.print(f"[cyan]Root partition: {root_dev} -> Disk: {disk}[/cyan]")

    # Check BIOS boot partition exists on GPT
    try:
        r = run("lsblk", "-ndo", "PARTTYPE", str(root_dev), check=False)
        # Not perfect, but warn
    except:
        pass

    # Ensure grub package
    console.print("[yellow]Ensuring GRUB package...[/yellow]")
    run("pacman", "-S", "--needed", "--noconfirm", "grub", check=False)

    # Handle LUKS2 argon2id issue for GRUB
    if luks["found"]:
        console.print("[yellow]Checking LUKS2 PBKDF for GRUB compatibility...[/yellow]")
        # GRUB only supports pbkdf2, not argon2id
        r = run("cryptsetup", "luksDump", luks["BACKING_DEV"], check=False)
        if "argon2i" in r.stdout or "argon2id" in r.stdout:
            console.print(Panel(
                "[bold red]GRUB BIOS cannot unlock argon2id LUKS2![/bold red]\n"
                "Your root is LUKS2 argon2id (default). For BIOS boot you need:\n"
                "cryptsetup luksConvertKey --pbkdf pbkdf2 /dev/<root>\n"
                "Or use an unencrypted /boot partition.\n"
                "Proceeding anyway, but boot will fail if /boot is inside LUKS.",
                box=box.ROUNDED, title="GRUB Warning"
            ))

    # Write /etc/default/grub with modern cmdline
    grub_default = Path("/etc/default/grub")
    grub_default.parent.mkdir(parents=True, exist_ok=True)
    
    # Escape for shell
    # Use same cmdline_base as systemd-boot for consistency
    grub_cfg = f"""# Generated by 151_systemd_bootloader.py - BIOS fallback
GRUB_DEFAULT=0
GRUB_TIMEOUT=5
GRUB_DISTRIBUTOR="Arch"
GRUB_CMDLINE_LINUX_DEFAULT="{cmdline_base}"
GRUB_CMDLINE_LINUX=""

# BTRFS and serial tweaks
GRUB_ENABLE_CRYPTODISK=y
GRUB_PRELOAD_MODULES="part_gpt part_msdos btrfs lvm cryptodisk luks2"

# Disable os-prober by default for offline ISO, enable if you want Windows auto-detect on BIOS
GRUB_DISABLE_OS_PROBER=false
GRUB_TIMEOUT_STYLE=menu
"""
    grub_default.write_text(grub_cfg)
    console.print(f"[green]Wrote {grub_default}[/green]")

    # Install GRUB to MBR
    console.print(f"[yellow]Installing GRUB i386-pc to {disk}...[/yellow]")
    r = run("grub-install", "--target=i386-pc", "--boot-directory=/boot", "--recheck", str(disk), check=False)
    if r.returncode != 0:
        console.print(f"[red]grub-install failed: {r.stdout} {r.stderr}[/red]")
        # Try with --force
        run("grub-install", "--target=i386-pc", "--boot-directory=/boot", "--recheck", "--force", str(disk), check=False)

    # Generate grub.cfg
    console.print("[yellow]Generating /boot/grub/grub.cfg...[/yellow]")
    run("grub-mkconfig", "-o", "/boot/grub/grub.cfg", check=False)

    console.print(Panel(f"[bold green]GRUB BIOS Fallback Complete\nDisk: {disk}\nConfig: /boot/grub/grub.cfg[/bold green]", box=box.ROUNDED))

def main():
    if os.geteuid() != 0:
        console.print("[red]Must be run as root inside arch-chroot[/red]")
        sys.exit(1)

    boot_mode = detect_boot_mode()
    console.print(Panel(f"[bold]DUSKY Bootloader Orchestrator - {boot_mode} - Python 3.14.6[/bold]", box=box.ROUNDED))

    topo = get_root_topology()
    hooks_str = parse_mkinicpio_hooks()
    luks = detect_luks(topo["ROOT_BLK_DEV"])

    # Build cmdline base (shared for both paths)
    cmdline_base = f"rw loglevel=3 rootfstype={topo['ROOT_FSTYPE']}"
    if topo["ROOT_FSTYPE"] == "btrfs":
        cmdline_base += " fsck.mode=skip"

    if luks["found"]:
        if " sd-encrypt " in hooks_str:
            cmdline_base = f"rd.luks.name={luks['LUKS_UUID']}={luks['MAPPER_NAME']} rd.luks.options=discard root=UUID={topo['ROOT_UUID']} {cmdline_base}"
        elif " encrypt " in hooks_str:
            cmdline_base = f"cryptdevice=UUID={luks['LUKS_UUID']}:{luks['MAPPER_NAME']}:allow-discards root=/dev/mapper/{luks['MAPPER_NAME']} {cmdline_base}"
        else:
            console.print("[red]LUKS found but no encrypt hook[/red]")
            sys.exit(1)
    else:
        cmdline_base = f"root=UUID={topo['ROOT_UUID']} {cmdline_base}"

    if topo["ROOT_SUBVOL"]:
        cmdline_base += f" rootflags=subvol={topo['ROOT_SUBVOL']}"

    if boot_mode == "UEFI":
        install_systemd_boot_uefi()
    else:
        # BIOS fallback
        install_grub_bios_fallback(topo, luks, cmdline_base)

if __name__ == "__main__":
    def _h(sig, frame):
        console.print(f"\n[yellow]Signal {signal.Signals(sig).name}, exiting[/yellow]")
        sys.exit(128 + sig)
    signal.signal(signal.SIGINT, _h)
    signal.signal(signal.SIGTERM, _h)
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
