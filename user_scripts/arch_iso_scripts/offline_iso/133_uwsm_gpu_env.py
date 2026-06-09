#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# Arch / Hyprland / UWSM GPU Configurator (v2026.08-Golden-Python-V2)
# -----------------------------------------------------------------------------
# Role:       System Architect
# Objective:  Topology selection + Active Dependency Management + Safe AQ mapping.
# Constraint: Safe for both Live/Chroot environments and active Host sessions.
# Standards:  Python 3.14+, Sysfs Parsing, Atomic Writes, Idempotency.
# -----------------------------------------------------------------------------

import argparse
import dataclasses
import os
import pwd
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

# ANSI Escape Sequences for terminal styling
class Log:
    BOLD = '\033[1m'
    BLUE = '\033[34m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    RED = '\033[31m'
    RESET = '\033[0m'

    @classmethod
    def info(cls, msg: str): print(f"{cls.BLUE}{cls.BOLD}[INFO]{cls.RESET} {msg}")
    @classmethod
    def ok(cls, msg: str):   print(f"{cls.GREEN}{cls.BOLD}[OK]{cls.RESET} {msg}")
    @classmethod
    def warn(cls, msg: str): print(f"{cls.YELLOW}{cls.BOLD}[WARN]{cls.RESET} {msg}", file=sys.stderr)
    @classmethod
    def err(cls, msg: str):  print(f"{cls.RED}{cls.BOLD}[ERROR]{cls.RESET} {msg}", file=sys.stderr)

@dataclasses.dataclass
class GPUCard:
    dev_node: Path
    vendor_id: str
    vendor_label: str
    name: str
    pci_address: str
    by_path: Path
    boot_vga: bool

def parse_args():
    parser = argparse.ArgumentParser(description="Configure UWSM GPU environment variables.")
    parser.add_argument("--auto", action="store_true", help="Automatically select the best primary GPU")
    parser.add_argument("--user", type=str, help="Specific target username (optional, defaults to auto-discovering all human users)")
    return parser.parse_args()

def check_deps():
    if shutil.which("lspci"):
        return

    Log.warn("Missing dependency detected: pciutils")
    Log.info("Attempting to install via pacman...")

    try:
        if os.geteuid() == 0:
            subprocess.run(['pacman', '-S', '--needed', '--noconfirm', 'pciutils'], check=True)
        else:
            if not shutil.which("sudo"):
                Log.err("sudo is required to install missing packages.")
                sys.exit(1)
            subprocess.run(['sudo', 'pacman', '-S', '--needed', '--noconfirm', 'pciutils'], check=True)
        Log.ok("Dependencies installed successfully.")
    except subprocess.CalledProcessError:
        Log.err("Failed to install required dependencies. Aborting.")
        sys.exit(1)

def get_vendor_label(vendor_id: str) -> str:
    match vendor_id.lower():
        case "0x8086": return "Intel"
        case "0x1002": return "AMD"
        case "0x10de": return "NVIDIA"
        case _: return f"Vendor {vendor_id}"

def get_pci_name(pci_address: str) -> str:
    try:
        result = subprocess.run(['lspci', '-s', pci_address], capture_output=True, text=True, check=True)
        # Matches the textual description after the PCI bus address
        match = re.search(r'^[0-9a-fA-F:\.]+ [^:]+: (.*)', result.stdout)
        if match:
            return match.group(1).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return "Unknown PCI Device"

def detect_topology() -> list[GPUCard]:
    Log.info("Scanning GPU topology via sysfs...")
    drm_path = Path("/sys/class/drm")
    cards: list[GPUCard] = []

    if not drm_path.exists():
        Log.err("No /sys/class/drm directory found. Is sysfs mounted? (Are you outside arch-chroot without mounts?)")
        sys.exit(1)

    for card_dir in drm_path.iterdir():
        if not re.match(r'^card\d+$', card_dir.name):
            continue

        vendor_file = card_dir / "device" / "vendor"
        if not vendor_file.is_file():
            continue

        dev_node = Path(f"/dev/dri/{card_dir.name}")
        if not dev_node.exists():
            continue

        try:
            sys_device_path = (card_dir / "device").resolve(strict=True)
        except OSError:
            Log.warn(f"Skipping unreadable DRM device path: {card_dir}")
            continue

        pci_address = sys_device_path.name
        vendor_id = vendor_file.read_text().strip().lower()

        boot_vga_file = card_dir / "device" / "boot_vga"
        boot_vga = boot_vga_file.read_text().strip() == "1" if boot_vga_file.is_file() else False

        by_path = Path(f"/dev/dri/by-path/pci-{pci_address}-card")
        human_name = get_pci_name(pci_address)
        vendor_label = get_vendor_label(vendor_id)

        cards.append(GPUCard(
            dev_node=dev_node,
            vendor_id=vendor_id,
            vendor_label=vendor_label,
            name=human_name,
            pci_address=pci_address,
            by_path=by_path,
            boot_vga=boot_vga
        ))

    if not cards:
        Log.err("No usable GPUs detected in /sys/class/drm.")
        sys.exit(1)
    
    # Guarantee consistent PCI sorting ordering exactly mimicking bash `sort`
    cards.sort(key=lambda c: c.pci_address)
    return cards

def determine_default_primary(cards: list[GPUCard]) -> tuple[GPUCard, str]:
    boot_cards = [c for c in cards if c.boot_vga]
    
    match len(boot_cards):
        case 0:
            return cards[0], "No boot_vga GPU reported; defaulting to lowest PCI address"
        case 1:
            return boot_cards[0], "Primary boot_vga hardware mapping"
        case _:
            # Returns [0] here because `cards` (and therefore `boot_cards`) is pre-sorted by PCI Address
            return boot_cards[0], "Multiple boot_vga GPUs reported; defaulting to lowest PCI address"

def print_topology(cards: list[GPUCard], default_card: GPUCard):
    print(f"\n{Log.BOLD}--- GPU Topology Detected ---{Log.RESET}")
    for card in cards:
        markers = []
        if card.boot_vga:
            markers.append(f"{Log.YELLOW}[boot_vga]{Log.RESET}")
        if card is default_card:
            markers.append(f"{Log.GREEN}[default]{Log.RESET}")
        
        marker_str = " ".join(markers)
        if marker_str:
            marker_str = " " + marker_str

        print(f"  • {Log.BOLD}{card.dev_node}{Log.RESET}{marker_str}")
        print(f"      ├─ Name: {card.name}")
        print(f"      ├─ PCI : {card.pci_address}")
        print(f"      └─ Link: {card.by_path if card.by_path.exists() else 'unavailable'}")
    print()

def select_primary_gpu(cards: list[GPUCard], auto_mode: bool) -> tuple[GPUCard, str]:
    default_card, default_reason = determine_default_primary(cards)
    
    if len(cards) == 1:
        Log.info(f"Single GPU detected; using {default_card.dev_node}.")
        return default_card, "single"
        
    print_topology(cards, default_card)
    
    if auto_mode:
        Log.info(f"Auto-selected primary GPU based on: {default_reason}.")
        return default_card, "auto"

    default_index = cards.index(default_card) + 1
    
    print("Select the GPU that should drive Hyprland.\n")
    for idx, card in enumerate(cards, start=1):
        marker = f" {Log.GREEN}[default]{Log.RESET}" if card is default_card else ""
        print(f"  {idx}) {card.dev_node} ({card.vendor_label}){marker}")
        print(f"      {card.name}")
    print()

    try:
        choice_str = input(f"Enter choice [{default_index}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        Log.warn("Input closed; using default selection.")
        choice_str = ""

    if not choice_str:
        choice_idx = default_index
    elif choice_str.isdigit() and 1 <= int(choice_str) <= len(cards):
        choice_idx = int(choice_str)
    else:
        Log.warn("Invalid selection. Using default.")
        choice_idx = default_index

    selected = cards[choice_idx - 1]
    Log.info(f"Selected primary GPU: {selected.dev_node} ({selected.name}).")
    return selected, "manual"

def build_aq_runtime_string(primary: GPUCard, all_cards: list[GPUCard]) -> str:
    # Ordered array where Primary is evaluated first by Aquamarine
    ordered_cards = [primary] + [c for c in all_cards if c is not primary]
    parts = []
    
    for card in ordered_cards:
        if card.by_path.exists():
            # Generates exact bash equivalent: $(readlink -f -- '/dev/dri/by-path/...') for UWSM evaluation
            parts.append(f"$(readlink -f -- {shlex.quote(str(card.by_path))})")
        else:
            Log.warn(f"Missing by-path symlink for {card.dev_node}; falling back to node name.")
            parts.append(str(card.dev_node))
            
    return ":".join(parts)

def get_target_users(target_user: Optional[str]) -> list[pwd.struct_passwd]:
    if target_user:
        try:
            return [pwd.getpwnam(target_user)]
        except KeyError:
            Log.err(f"Target user '{target_user}' not found on the system.")
            sys.exit(1)
    
    if os.geteuid() != 0:
        # Running as a normal user (outside chroot), configure just for the current user
        return [pwd.getpwuid(os.getuid())]
        
    # Running as root without a specific user: target all human users (UID >= 1000)
    users = []
    for p in pwd.getpwall():
        # Standard Arch Linux human users are 1000-59999, exclude system/nobody accounts
        if 1000 <= p.pw_uid < 60000 and Path(p.pw_dir).is_dir():
            users.append(p)
            
    if not users:
        Log.warn("No regular human users (UID >= 1000) found. Falling back to root configuration.")
        return [pwd.getpwuid(0)]
        
    return users

def ensure_dir_with_ownership(path: Path, uid: int, gid: int):
    # Generates only missing directories up the tree and enforces exact system user ownership
    dirs_to_chown = []
    current = path
    while not current.exists():
        dirs_to_chown.append(current)
        current = current.parent
        
    path.mkdir(parents=True, exist_ok=True)
    for d in reversed(dirs_to_chown):
        os.chown(d, uid, gid)

def generate_config(primary: GPUCard, all_cards: list[GPUCard], mode: str, args: argparse.Namespace):
    aq_runtime_string = build_aq_runtime_string(primary, all_cards)
    
    dri_dir = Path("/usr/lib/dri")
    vaapi_lines = []
    
    match primary.vendor_id:
        case "0x8086":
            vaapi_lines.append("# Intel Media Session")
            if (dri_dir / "iHD_drv_video.so").exists():
                vaapi_lines.append("export LIBVA_DRIVER_NAME=iHD")
            elif (dri_dir / "i965_drv_video.so").exists():
                vaapi_lines.append("export LIBVA_DRIVER_NAME=i965")
        case "0x1002":
            vaapi_lines.append("# AMD Media Session")
            if (dri_dir / "radeonsi_drv_video.so").exists():
                vaapi_lines.append("export LIBVA_DRIVER_NAME=radeonsi")
        case "0x10de":
            # Chroot execution means we check for installed user-space libraries directly
            # rather than querying sysfs/lsmod which only map the host's kernel status.
            if Path("/usr/lib/gbm/nvidia-drm_gbm.so").exists():
                vaapi_lines.append("# NVIDIA Primary Session (Proprietary)")
                vaapi_lines.append("export GBM_BACKEND=nvidia-drm")
                vaapi_lines.append("export __GLX_VENDOR_LIBRARY_NAME=nvidia")
                if (dri_dir / "nvidia_drv_video.so").exists():
                    vaapi_lines.append("export LIBVA_DRIVER_NAME=nvidia")
            else:
                vaapi_lines.append("# NVIDIA Primary Session (Nouveau)")
                vaapi_lines.append("export MESA_LOADER_DRIVER_OVERRIDE=nouveau")
                if (dri_dir / "nouveau_drv_video.so").exists():
                    vaapi_lines.append("export LIBVA_DRIVER_NAME=nouveau")

    config_content = [
        "# -----------------------------------------------------------------",
        f"# UWSM GPU Config | Mode: {mode.upper()}",
        f"# Primary DRM node: {primary.dev_node}",
        f"# Primary GPU: {primary.vendor_label} | {primary.name} | {primary.pci_address}",
        "# -----------------------------------------------------------------",
        "export ELECTRON_OZONE_PLATFORM_HINT=wayland",
        "export MOZ_ENABLE_WAYLAND=1",
        "",
        "# Hyprland / Aquamarine GPU priority",
        "# Resolved dynamically at session start to avoid colon-parsing bugs.",
        f'export AQ_DRM_DEVICES="{aq_runtime_string}"',
        ""
    ]
    
    if vaapi_lines:
        config_content.extend(vaapi_lines)
        config_content.append("")
        
    config_text = "\n".join(config_content)
    
    target_users = get_target_users(args.user)
    output_files = []

    for user in target_users:
        uwsm_dir = Path(user.pw_dir) / ".config" / "uwsm"
        env_dir = uwsm_dir / "env.d"
        output_file = env_dir / "gpu"
        
        ensure_dir_with_ownership(env_dir, user.pw_uid, user.pw_gid)
        
        # Atomic write pattern mapping 1-to-1 against bash standard tmp generation rules
        fd, tmp_path_str = tempfile.mkstemp(dir=env_dir, prefix=".gpu.")
        tmp_path = Path(tmp_path_str)
        
        try:
            with os.fdopen(fd, 'w') as f:
                f.write(config_text)
                
            os.chmod(tmp_path, 0o644)
            os.chown(tmp_path, user.pw_uid, user.pw_gid)
            
            # Matches idempotent cmp testing
            if output_file.exists() and output_file.read_text() == config_text:
                tmp_path.unlink()
                Log.ok(f"Config is strictly optimal and up to date for {user.pw_name}: {output_file}")
            else:
                shutil.move(tmp_path, output_file)
                Log.ok(f"Config generated and securely written for {user.pw_name}: {output_file}")
                
            output_files.append(output_file)
        except Exception as e:
            if tmp_path.exists():
                tmp_path.unlink()
            Log.err(f"Failed writing configuration for {user.pw_name}: {e}")
            
    if not output_files:
        Log.err("No configuration files were written.")
        sys.exit(1)
        
    return output_files[0]

def preview_config(output_file: Path):
    Log.info("Previewing active config parameters:")
    print("-------------------------------------")
    if output_file.exists():
        for line in output_file.read_text().splitlines():
            if any(key in line for key in ["AQ_DRM_DEVICES", "GBM_BACKEND", "__GLX_VENDOR_LIBRARY_NAME", "MESA_LOADER_DRIVER_OVERRIDE", "LIBVA_DRIVER_NAME", "Mode:", "Primary DRM node:", "Primary GPU:"]):
                print(line)
    print("-------------------------------------")

def main():
    args = parse_args()
    Log.info("Starting Elite DevOps GPU Configuration (Python V2)...")
    check_deps()
    cards = detect_topology()
    primary_card, mode = select_primary_gpu(cards, args.auto)
    output_file = generate_config(primary_card, cards, mode, args)
    preview_config(output_file)
    Log.ok("Done. Please restart your UWSM session (or complete installation).")

if __name__ == "__main__":
    main()
