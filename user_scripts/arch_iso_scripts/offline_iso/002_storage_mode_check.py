#!/usr/bin/env python3
# DUSKY_INTERACTIVE=false
"""
002_storage_mode_check.py - DUSKY Storage Mode & VMD Diagnostics (Kernel 7.1+ & Python 3.14.6+)
Role:       Storage Architecture & BIOS Configuration Assister
Context:    Executes immediately after 001_uefi_check.sh in Phase 1 (ISO).
Objective:  Detects Intel RST RAID, AMD RAIDXpert2, and Intel VMD remapped storage modes.
            Auto-probes VMD module, filters out live ISO boot media & USB drives,
            verifies internal target drives, and alerts user with exact BIOS instructions if RAID mode blocks drive access.
Standards:  Python 3.14.6+, Linux 7.1+ Sysfs/Dmesg Forensics, Zero Process Leaks, Rich TUI & JSON Output.
"""

from __future__ import annotations
import os
import sys
import re
import json
import time
import argparse
import subprocess
from pathlib import Path
from typing import Dict, List, Set, Optional, Any

def _ensure_rich():
    import importlib.util
    if importlib.util.find_spec("rich") is None:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            subprocess.run(["pacman", "-Sy", "--needed", "--noconfirm", "python-rich"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

_ensure_rich()

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.align import Align
from rich.text import Text
from rich import box

def make_console() -> Console:
    term = os.environ.get("TERM", "")
    if term in ("dumb", "unknown", ""):
        os.environ["TERM"] = "linux"
        return Console(color_system="truecolor", force_terminal=None, legacy_windows=False, safe_box=False, highlight=False, markup=True)
    return Console(color_system="auto", force_terminal=None, legacy_windows=False, safe_box=False, highlight=False, markup=True)

console = make_console()

# Known PCI Device IDs for Intel VMD & SATA/NVMe RAID controllers
INTEL_VMD_DEVICES: Set[str] = {
    "9a0b", "a77f", "467f", "09ab", "2822", "282a", "7a82", "a708", "43b5", "020d", "9a09"
}

AMD_RAID_DEVICES: Set[str] = {
    "7916", "43bd", "43c8", "43e2"
}

def verify_kernel_version() -> str:
    release = os.uname().release
    match = re.match(r"^(\d+)\.(\d+)", release)
    if not match:
        return release
    major, minor = int(match.group(1)), int(match.group(2))
    if (major, minor) < (7, 1):
        console.print(f"[bold red][ERROR][/bold red] Strictly targeted for Linux Kernel 7.1+. Detected: {release}")
        sys.exit(3)
    return release

def get_live_boot_devices() -> Set[str]:
    """
    Finds the block device(s) hosting the live Arch ISO (/run/archiso/bootmnt, etc.)
    to ensure installer USB drives are never mistaken for internal target drives.
    """
    boot_devs: Set[str] = set()
    mount_points = ["/run/archiso/bootmnt", "/run/archiso/cowspace", "/run/archiso/airootfs", "/run/archiso/sfs/airootfs"]

    for mp in mount_points:
        if Path(mp).exists():
            try:
                r = subprocess.run(["findmnt", "-rn", "-o", "SOURCE", "-T", mp], capture_output=True, text=True, check=False)
                if r.returncode == 0 and r.stdout.strip():
                    src = r.stdout.strip().split("[")[0]
                    boot_devs.add(Path(src).name)
            except Exception:
                pass
            try:
                r = subprocess.run(["lsblk", "-no", "PKNAME,NAME", mp], capture_output=True, text=True, check=False)
                if r.returncode == 0:
                    for line in r.stdout.splitlines():
                        for token in line.split():
                            token = token.strip()
                            if token and not token.startswith("loop") and not token.startswith("dm-"):
                                boot_devs.add(token)
            except Exception:
                pass

    for mapper in ("ventoy", "sda", "sdb"):
        if Path(f"/dev/mapper/{mapper}").exists():
            boot_devs.add(mapper)

    return boot_devs

def is_usb_transport(dev_name: str) -> bool:
    """
    Robust check via udevadm & sysfs to determine if a block device uses USB bus.
    Catches USB flash drives and USB-to-NVMe / USB-to-SATA external enclosures.
    """
    sys_dev = Path(f"/sys/class/block/{dev_name}/device")
    if sys_dev.exists():
        try:
            resolved = sys_dev.resolve().as_posix()
            if "/usb" in resolved or ("/host" in resolved and "usb" in resolved):
                return True
        except Exception:
            pass

    try:
        r = subprocess.run(["udevadm", "info", "-q", "property", "-n", f"/dev/{dev_name}"], capture_output=True, text=True, check=False)
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                if line.startswith(("ID_BUS=usb", "ID_USB_DRIVER=")) or ("DEVPATH=" in line and "/usb" in line):
                    return True
    except Exception:
        pass

    try:
        r = subprocess.run(["lsblk", "-ndo", "TRAN", f"/dev/{dev_name}"], capture_output=True, text=True, check=False)
        if r.returncode == 0 and r.stdout.strip().lower() == "usb":
            return True
    except Exception:
        pass

    return False

def classify_drive_type(dev_name: str) -> str:
    if dev_name.startswith("nvme"):
        return "NVMe SSD"
    elif dev_name.startswith("sd"):
        rot_file = Path(f"/sys/class/block/{dev_name}/queue/rotational")
        if rot_file.exists():
            try:
                rot = int(rot_file.read_text().strip())
                return "SATA HDD" if rot == 1 else "SATA SSD"
            except Exception:
                pass
        return "SATA/SCSI Drive"
    elif dev_name.startswith("mmcblk"):
        return "eMMC / SD Storage"
    elif dev_name.startswith("vd"):
        return "VirtIO Disk"
    elif dev_name.startswith("xvd"):
        return "Xen Disk"
    return "Block Storage"

def find_storage_drives() -> List[Dict[str, Any]]:
    drives: List[Dict[str, Any]] = []
    sys_block = Path("/sys/class/block")
    if not sys_block.exists():
        return drives

    live_boot_devs = get_live_boot_devices()

    for dev_path in sys_block.iterdir():
        name = dev_path.name

        # Skip virtual loop, zram, ram, optical, nbd, dm devices
        if name.startswith(("loop", "zram", "ram", "sr", "nbd", "dm-")):
            continue

        # Skip partition nodes (e.g., sda1, nvme0n1p1, mmcblk0p1)
        if (dev_path / "partition").exists():
            continue

        # Skip live ISO installation media
        if name in live_boot_devs:
            continue

        size_file = dev_path / "size"
        if not size_file.exists():
            continue

        try:
            size_bytes = int(size_file.read_text().strip()) * 512
        except ValueError:
            continue

        # Ignore small drives < 1GB
        if size_bytes < 1 * 1024 * 1024 * 1024:
            continue

        removable = 0
        rem_file = dev_path / "removable"
        if rem_file.exists():
            try:
                removable = int(rem_file.read_text().strip())
            except ValueError:
                removable = 0

        is_usb = is_usb_transport(name)
        drive_type = classify_drive_type(name)

        # Read model string if available
        model = ""
        model_file = dev_path / "device" / "model"
        if model_file.exists():
            try:
                model = model_file.read_text().strip()
            except Exception:
                model = ""

        drives.append({
            "name": name,
            "path": f"/dev/{name}",
            "size_bytes": size_bytes,
            "size_gb": round(size_bytes / (1024**3), 2),
            "is_removable": removable == 1,
            "is_usb": is_usb,
            "type": drive_type,
            "model": model or drive_type
        })

    return drives

def scan_pci_controllers() -> Dict[str, Any]:
    controllers: List[Dict[str, Any]] = []
    pci_dir = Path("/sys/bus/pci/devices")
    has_intel_raid = False
    has_amd_raid = False
    has_vmd_hardware = False
    vmd_driver_loaded = False

    if pci_dir.exists():
        for pci_dev in pci_dir.iterdir():
            try:
                class_code = (pci_dev / "class").read_text().strip().lower()
                if not class_code.startswith("0x01"):
                    continue

                vendor = (pci_dev / "vendor").read_text().strip().lower()
                vendor_hex = vendor.replace("0x", "")
                device = (pci_dev / "device").read_text().strip().lower()
                device_hex = device.replace("0x", "")

                driver_link = pci_dev / "driver"
                driver = driver_link.resolve().name if driver_link.exists() else "none"

                is_intel = (vendor_hex == "8086")
                is_amd = (vendor_hex in ("1002", "1022"))
                is_raid_class = class_code.startswith("0x0104")
                is_vmd_dev = is_intel and (device_hex in INTEL_VMD_DEVICES or driver == "vmd")

                if is_intel and (is_raid_class or device_hex in ("2822", "282a")):
                    has_intel_raid = True

                if is_amd and (is_raid_class or device_hex in AMD_RAID_DEVICES):
                    has_amd_raid = True

                if is_vmd_dev:
                    has_vmd_hardware = True
                    if driver == "vmd":
                        vmd_driver_loaded = True

                controllers.append({
                    "pci_id": pci_dev.name,
                    "class": class_code,
                    "vendor": vendor,
                    "device": device,
                    "driver": driver,
                    "is_intel": is_intel,
                    "is_amd": is_amd,
                    "is_raid": is_raid_class,
                    "is_vmd": is_vmd_dev
                })
            except OSError:
                continue

    return {
        "controllers": controllers,
        "has_intel_raid": has_intel_raid,
        "has_amd_raid": has_amd_raid,
        "has_vmd_hardware": has_vmd_hardware,
        "vmd_driver_loaded": vmd_driver_loaded
    }

def inspect_kernel_dmesg() -> Dict[str, Any]:
    info = {
        "remapped_nvme_found": False,
        "bios_raid_warning": False,
        "amd_raid_warning": False,
        "matches": []
    }
    try:
        r = subprocess.run(["dmesg"], capture_output=True, text=True, check=False)
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                line_str = line.strip()
                if any(kw in line_str for kw in ["remapped NVMe", "Switch your BIOS from RAID", "Intel RST", "rcraid", "RAIDXpert2"]):
                    info["matches"].append(line_str)
                    if "remapped NVMe" in line_str:
                        info["remapped_nvme_found"] = True
                    if "Switch your BIOS from RAID" in line_str:
                        info["bios_raid_warning"] = True
                    if "rcraid" in line_str or "RAIDXpert2" in line_str:
                        info["amd_raid_warning"] = True
    except Exception:
        pass
    return info

def probe_vmd_module(quiet: bool = False) -> bool:
    if not quiet:
        console.print("  [cyan]->[/cyan] Attempting kernel auto-probe for Intel VMD module ([bold]modprobe vmd[/bold])...")
    try:
        subprocess.run(["modprobe", "vmd"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["udevadm", "settle", "--timeout=3"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)
        return True
    except Exception:
        return False

def print_alert_and_instructions(dmesg_info: Dict[str, Any], pci_summary: Dict[str, Any]):
    title = Text.from_markup("[bold red]CRITICAL: INTERNAL STORAGE HIDDEN BY BIOS RAID / VMD MODE[/bold red]", justify="center")
    
    body = Text()
    body.append("\n[!] The installer cannot detect your internal SSD/HDD drive.\n\n", style="bold yellow")
    
    if dmesg_info.get("matches"):
        body.append("Kernel 7.1 Forensics:\n", style="bold cyan")
        for match in dmesg_info["matches"]:
            body.append(f"  • {match}\n", style="red")
        body.append("\n")

    mode_name = "Intel RST RAID / VMD" if pci_summary.get("has_intel_raid") or pci_summary.get("has_vmd_hardware") else "AMD RAIDXpert2"

    body.append("REQUIRED USER ACTION IN BIOS / UEFI:\n", style="bold white")
    body.append("  1. Reboot your system and press ", style="white")
    body.append("F2 / Del / F12", style="bold green")
    body.append(" to enter BIOS Setup.\n", style="white")
    body.append("  2. Locate ", style="white")
    body.append("Storage Configuration / SATA Operation / VMD Controller", style="bold cyan")
    body.append(".\n", style="white")
    body.append(f"  3. Change the storage mode from ", style="white")
    body.append(f'"{mode_name}"', style="bold red")
    body.append(" to ", style="white")
    body.append('"AHCI / NVMe" / "Disabled VMD"', style="bold green")
    body.append(".\n", style="white")
    body.append("  4. Save changes, exit BIOS, and boot back into this Arch ISO.\n\n", style="white")

    panel = Panel(body, title=title, box=box.ROUNDED, border_style="red", padding=(1, 2))
    console.print()
    console.print(Align.center(panel))

def print_detected_drives_table(drives: List[Dict[str, Any]]):
    table = Table(title="Detected Target Storage Drives", box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Device Node", style="bold green")
    table.add_column("Type", style="yellow")
    table.add_column("Capacity", style="cyan", justify="right")
    table.add_column("Model / Description", style="white")

    for d in drives:
        table.add_row(d["path"], d["type"], f"{d['size_gb']} GB", d["model"])

    console.print()
    console.print(Align.center(table))
    console.print()

def parse_args():
    parser = argparse.ArgumentParser(description="Dusky Storage Mode & VMD Diagnostics (Kernel 7.1+)")
    parser.add_argument("-a", "--auto", action="store_true", help="Run in autonomous mode")
    parser.add_argument("--json", action="store_true", help="Output JSON diagnostic report for GUI/automation")
    return parser.parse_args()

def main():
    args = parse_args()
    auto_mode = args.auto or os.environ.get("AUTO_MODE", "0") in ("1", "true", "TRUE")

    kernel_ver = verify_kernel_version()

    if not args.json:
        console.print(f"[bold cyan][INFO][/bold cyan] Storage Mode & VMD Diagnostics [dim](Kernel {kernel_ver})[/dim]")

    # Step 1: Initial drive scan
    drives = find_storage_drives()
    internal_drives = [d for d in drives if not d["is_removable"] and not d["is_usb"]]

    if internal_drives:
        if args.json:
            print(json.dumps({"status": "ok", "kernel": kernel_ver, "drives": internal_drives}))
        else:
            print_detected_drives_table(internal_drives)
            console.print(f"[bold green][OK][/bold green] Valid storage topography verified. Selected target: [cyan]{internal_drives[0]['path']}[/cyan]")
        sys.exit(0)

    # Step 2: Auto-probe Intel VMD module if no internal drive found
    probe_vmd_module(quiet=args.json)
    drives = find_storage_drives()
    internal_drives = [d for d in drives if not d["is_removable"] and not d["is_usb"]]

    if internal_drives:
        if args.json:
            print(json.dumps({"status": "ok", "vmd_probed": True, "kernel": kernel_ver, "drives": internal_drives}))
        else:
            print_detected_drives_table(internal_drives)
            console.print(f"[bold green][OK][/bold green] Storage drive detected after Intel VMD probe: [cyan]{internal_drives[0]['path']}[/cyan]")
        sys.exit(0)

    # Step 3: Deep PCI & Dmesg forensics
    pci_summary = scan_pci_controllers()
    dmesg_info = inspect_kernel_dmesg()

    is_raid_blocked = (
        pci_summary["has_intel_raid"] or
        pci_summary["has_amd_raid"] or
        pci_summary["has_vmd_hardware"] or
        dmesg_info["remapped_nvme_found"] or
        dmesg_info["bios_raid_warning"] or
        dmesg_info["amd_raid_warning"]
    )

    if is_raid_blocked:
        if args.json:
            print(json.dumps({
                "status": "bios_raid_blocked",
                "kernel": kernel_ver,
                "pci_summary": pci_summary,
                "dmesg_info": dmesg_info
            }))
        else:
            print_alert_and_instructions(dmesg_info, pci_summary)
        sys.exit(1)

    # Step 4: Generic missing storage drive failure
    if args.json:
        print(json.dumps({
            "status": "no_drives_found",
            "kernel": kernel_ver,
            "pci_summary": pci_summary,
            "dmesg_info": dmesg_info
        }))
    else:
        console.print("[bold red][ERROR][/bold red] No internal storage drives detected on the system. Please verify physical disk connections.")
    sys.exit(2)

if __name__ == "__main__":
    main()
