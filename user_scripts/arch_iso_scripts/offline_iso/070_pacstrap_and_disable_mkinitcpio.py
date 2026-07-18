#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# MODULE: PACSTRAP (HARDWARE-VERIFIED & OFFLINE CACHE EDITION)
# -----------------------------------------------------------------------------
# Role:       System Architect
# Objective:  Hybrid hardware mapping: Strict sysfs for GPUs, broad hwdata for 
#             peripherals. Flawless mkinitcpio hook masking.
# Standards:  Python 3.14+, Atomic State, Zero process leaks.
# -----------------------------------------------------------------------------

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

class Log:
    BOLD = '\033[1m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    RED = '\033[31m'
    RESET = '\033[0m'

    @classmethod
    def info(cls, msg: str): print(f"{cls.GREEN}{cls.BOLD}[INFO]{cls.RESET} {msg}")
    @classmethod
    def ok(cls, msg: str):   print(f"{cls.GREEN}{cls.BOLD}[OK]{cls.RESET} {msg}")
    @classmethod
    def warn(cls, msg: str): print(f"{cls.YELLOW}{cls.BOLD}[WARN]{cls.RESET} {msg}", file=sys.stderr)
    @classmethod
    def err(cls, msg: str):  print(f"{cls.RED}{cls.BOLD}[ERROR]{cls.RESET} {msg}", file=sys.stderr)

MOUNT_POINT = Path("/mnt")

# Base packages every system needs
FINAL_PACKAGES = [
    "base", "base-devel", "linux", "linux-headers", "mkinitcpio",
    "neovim", "btrfs-progs", "dosfstools", "git", "zsh",
    "networkmanager", "yazi", "linux-firmware-other", "otf-atkinsonhyperlegiblemono-nerd",
    "python", "python-textual", "python-rich"
]

def wait_for_pacman_lock():
    lock_file = Path("/var/lib/pacman/db.lck")
    while lock_file.exists():
        Log.warn("Waiting for pacman lock...")
        time.sleep(3)

class HardwareScanner:
    def __init__(self):
        self.vendors = set()
        self.pci_devices = []  # (vendor_id, class_code)
        self.hw_text_cache = ""
        self.vm_detected = False
        
        self.ensure_live_tool("lspci", "pciutils")
        self.ensure_live_tool("lsusb", "usbutils")

        self.scan_sysfs_pci()
        self.scan_sysfs_usb()
        self.build_text_cache()
        self.detect_vm()

    def ensure_live_tool(self, cmd: str, pkg: str):
        if not shutil.which(cmd):
            wait_for_pacman_lock()
            subprocess.run(["pacman", "-S", "--noconfirm", "--needed", pkg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def scan_sysfs_pci(self):
        """Strict hex mapping for bare-metal graphics isolation"""
        pci_path = Path("/sys/bus/pci/devices")
        if pci_path.exists():
            for pci_dev in pci_path.iterdir():
                try:
                    vendor = (pci_dev / "vendor").read_text().strip().lower()
                    class_code = (pci_dev / "class").read_text().strip().lower()
                    self.vendors.add(vendor)
                    self.pci_devices.append((vendor, class_code))
                except OSError:
                    continue

    def scan_sysfs_usb(self):
        usb_path = Path("/sys/bus/usb/devices")
        if usb_path.exists():
            for usb_dev in usb_path.iterdir():
                try:
                    vendor = "0x" + (usb_dev / "idVendor").read_text().strip().lower()
                    self.vendors.add(vendor)
                except OSError:
                    continue

    def build_text_cache(self):
        """Builds a human-readable string cache equivalent to the Bash script"""
        pci_data = subprocess.run(["lspci", "-mm"], capture_output=True, text=True).stdout
        usb_data = subprocess.run(["lsusb"], capture_output=True, text=True).stdout
        self.hw_text_cache = f"{pci_data}\n{usb_data}"

    def detect_vm(self):
        vm_vendors = {"0x1af4", "0x15ad", "0x80ee"}
        if self.vendors.intersection(vm_vendors):
            self.vm_detected = True
            return

        vm_indicators = ["qemu", "virtualbox", "vmware", "innotek", "bochs", "kvm", "virtio"]
        if any(indicator in self.hw_text_cache.lower() for indicator in vm_indicators):
            self.vm_detected = True

    def has_pci(self, target_vendor: str, target_class_prefix: str = None) -> bool:
        """Used strictly for GPUs to prevent false positives"""
        for vendor, class_code in self.pci_devices:
            if vendor == target_vendor:
                if target_class_prefix is None or class_code.startswith(target_class_prefix):
                    return True
        return False

    def has_text_match(self, pattern: str) -> bool:
        """Used for peripherals to maximize OEM compatibility"""
        return bool(re.search(pattern, self.hw_text_cache, re.IGNORECASE))

def parse_args():
    parser = argparse.ArgumentParser(description="Pacstrap Hardware-Verified Installer")
    parser.add_argument("-a", "--auto", action="store_true", help="Run autonomously (no prompts)")
    parser.add_argument("--arch", action="store_true", default=True, help="Target standard Arch Linux")
    parser.add_argument("--cachyos", action="store_true", help="Target CachyOS")
    return parser.parse_args()

def package_exists(pkg: str) -> bool:
    wait_for_pacman_lock()
    result = subprocess.run(["pacman", "-Si", pkg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0

def prompt_yes_no(prompt: str, default: str = "N") -> bool:
    choices = " [Y/n] " if default.upper() == "Y" else " [y/N] "
    reply = input(prompt + choices).strip().lower()
    if not reply:
        return default.upper() == "Y"
    return reply in ("y", "yes")

def get_cpu_ucode() -> str:
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if line.startswith("vendor_id"):
                    vendor = line.split(":")[1].strip()
                    match vendor:
                        case "GenuineIntel": return "intel-ucode"
                        case "AuthenticAMD": return "amd-ucode"
                    break
    except OSError:
        pass
    return None

def normalize_target_tmp_permissions():
    tmp_dir = MOUNT_POINT / "var" / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.chmod(0o1777)

def force_symlink(src: Path, dst: Path):
    """Mathematically mirrors 'ln -sf' behavior."""
    if dst.is_symlink() or dst.exists():
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst)
        else:
            dst.unlink(missing_ok=True)
            
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.symlink_to(src)

def mask_mkinitcpio_hooks():
    Log.info("Masking mkinitcpio ALPM hooks in both Host and Target environments...")
    
    hooks = ["90-mkinitcpio-install.hook", "60-mkinitcpio-remove.hook"]
    dev_null = Path("/dev/null")
    
    host_hook_dir = Path("/etc/pacman.d/hooks")
    for hook in hooks:
        force_symlink(dev_null, host_hook_dir / hook)

    target_hook_dir = MOUNT_POINT / "etc" / "pacman.d" / "hooks"
    for hook in hooks:
        force_symlink(dev_null, target_hook_dir / hook)

def main():
    args = parse_args()
    use_generic_firmware = False

    print(f"{Log.BOLD}=== PACSTRAP: HARDWARE-VERIFIED EDITION ==={Log.RESET}")

    if os.geteuid() != 0:
        Log.err("This script must be run as root.")
        sys.exit(1)

    if not MOUNT_POINT.is_mount():
        Log.err(f"{MOUNT_POINT} is not a mountpoint. Mount your partitions first.")
        sys.exit(1)

    auto_mode = args.auto
    if not auto_mode and sys.stdin.isatty():
        if prompt_yes_no("Enable autonomous mode for this run (skip all prompts)?", "N"):
            auto_mode = True
            Log.info("Autonomous mode enabled.")
    elif not sys.stdin.isatty():
        auto_mode = True
        Log.warn("Non-interactive session detected. Enabling autonomous mode.")

    # 1. CACHYOS PRE-REQUISITE INJECTION
    if args.cachyos:
        Log.info("CachyOS architecture selected. Injecting required keyrings and mirrors...")
        FINAL_PACKAGES.extend(["cachyos-keyring", "cachyos-mirrorlist", "cachyos-rate-mirrors"])

    # 2. CPU MICROCODE
    ucode = get_cpu_ucode()
    if ucode:
        Log.info(f"CPU Match: Queuing {ucode}")
        FINAL_PACKAGES.append(ucode)
    else:
        Log.warn("Unknown CPU Vendor. Proceeding without specific ucode.")

    # 3. PERIPHERAL DETECTION
    Log.info("Scanning Hardware Topography via Hybrid sysfs/hwdata engine...")
    hw = HardwareScanner()

    if hw.vm_detected:
        Log.warn("Virtual Machine detected. Bypassing bare-metal firmware discovery.")
    else:
        def check_and_add(name: str, pkg: str, found: bool):
            nonlocal use_generic_firmware
            print(f"   > Scanning for {name:<20}", end="")
            
            if use_generic_firmware:
                print(f"{Log.YELLOW}SKIPPED (Generic Mode){Log.RESET}")
                return

            if found:
                print(f"{Log.GREEN}FOUND{Log.RESET}")
                if package_exists(pkg):
                    print(f"     -> Queuing Verified Package: {Log.BOLD}{pkg}{Log.RESET}")
                    FINAL_PACKAGES.append(pkg)
                else:
                    print(f"     -> {Log.YELLOW}Hardware found, but package '{pkg}' missing in repo.{Log.RESET}")
                    print("     -> Switching to Safe Mode (Generic Firmware).")
                    use_generic_firmware = True
            else:
                print("NO")

        # -- GRAPHICS (Strict Sysfs Hex Verification) --
        check_and_add("Nvidia GPU", "linux-firmware-nvidia", hw.has_pci("0x10de", "0x03"))
        
        is_amd_gpu = hw.has_pci("0x1002", "0x03") or hw.has_pci("0x1022", "0x03")
        if is_amd_gpu:
            check_and_add("AMD GPU (Modern)", "linux-firmware-amdgpu", True)
            check_and_add("AMD GPU (Legacy)", "linux-firmware-radeon", True)
        else:
            check_and_add("AMD GPU", "linux-firmware-amdgpu", False)

        # -- NETWORKING & BLUETOOTH (Broad Regex Verification for OEM Compatibility) --
        check_and_add("Intel Network/BT", "linux-firmware-intel", hw.has_text_match(r"intel.*(network|wireless|bluetooth)|8086"))
        check_and_add("Mediatek WiFi/BT", "linux-firmware-mediatek", hw.has_text_match(r"mediatek"))
        check_and_add("Broadcom WiFi/BT", "linux-firmware-broadcom", hw.has_text_match(r"broadcom"))
        check_and_add("Atheros WiFi/BT", "linux-firmware-atheros", hw.has_text_match(r"atheros"))
        check_and_add("Realtek Eth/WiFi", "linux-firmware-realtek", hw.has_text_match(r"realtek|\brtl"))

        # -- AUDIO --
        check_and_add("Intel SOF Audio", "sof-firmware", hw.has_text_match(r"audio.*intel|8086"))
        check_and_add("Cirrus Logic Audio", "linux-firmware-cirrus", hw.has_text_match(r"cirrus"))

    # 4. FINAL PACKAGE ASSEMBLY
    if use_generic_firmware:
        Log.warn("Fallback Triggered: Consolidating to generic linux-firmware.")
        clean_list = [p for p in FINAL_PACKAGES if not (p.startswith("linux-firmware-") or p == "sof-firmware")]
        FINAL_PACKAGES.clear()
        FINAL_PACKAGES.extend(clean_list)
        FINAL_PACKAGES.append("linux-firmware")
    elif not hw.vm_detected:
        FINAL_PACKAGES.append("linux-firmware-whence")

    # Deduplicate while preserving order
    deduped_packages = list(dict.fromkeys(FINAL_PACKAGES))

    print("\n" + f"{Log.BOLD}Final Package List:{Log.RESET}")
    for pkg in deduped_packages:
        print(pkg)
    print()

    # 5. EXECUTION
    if not auto_mode:
        if not prompt_yes_no("Ready to run pacstrap?", "Y"):
            Log.warn("Aborted by user.")
            sys.exit(0)

    Log.info("Normalizing target temporary directory permissions...")
    normalize_target_tmp_permissions()

    mask_mkinitcpio_hooks()

    Log.info("Installing...")
    wait_for_pacman_lock()

    pacstrap_cmd = ["pacstrap", "-K", str(MOUNT_POINT)] + deduped_packages + ["--needed"]
    
    try:
        if auto_mode:
            # Ironclad scope management to ensure pipe stream destruction 
            yes_proc = subprocess.Popen(['yes', ''], stdout=subprocess.PIPE)
            try:
                subprocess.run(pacstrap_cmd, stdin=yes_proc.stdout, check=True)
            finally:
                yes_proc.terminate()
                yes_proc.wait()
        else:
            subprocess.run(pacstrap_cmd, check=True)
            
        print(f"\n{Log.GREEN}Pacstrap Complete.{Log.RESET}")
    except subprocess.CalledProcessError as e:
        Log.err(f"Pacstrap failed with exit code {e.returncode}")
        sys.exit(1)

if __name__ == "__main__":
    main()
