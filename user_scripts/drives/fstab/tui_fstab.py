#!/usr/bin/env python3
import json
import subprocess
import sys
from pathlib import Path

_dusky_root = Path.home() / "user_scripts" / "dusky_tui"
if str(_dusky_root) not in sys.path:
    sys.path.insert(0, str(_dusky_root))

import sys
from pathlib import Path

_DUSKY_TUI_ROOT = Path.home() / "user_scripts" / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem

ENGINE_TYPE = "fstab"
TARGET_FILE = "/etc/fstab"
APP_TITLE = "Dusky FSTAB Orchestrator"
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
REQUIRE_ROOT = True

TABS = [
    "Mount Info",
    "Filesystem",
    "BTRFS Ops",
    "System Flags"
]

SCHEMA = {i: [] for i in range(len(TABS))}

# --- TAB 0: MOUNT INFO ---
SCHEMA[0].append(ConfigItem(
    label="Target ID (UUID/Path)",
    key="uuid",
    scope="mount_info",
    type_="string",
    default="0000-0000-0000-0000",
    options=["0000-0000-0000-0000"],
    hints=["Placeholder target device ID"],
    extended_help=(
        "**Target ID (UUID/Path):** Select or enter the partition identifier. "
        "Can be a raw UUID, PARTUUID=, LABEL=, or absolute device path (e.g. `/dev/sda1`).\n\n"
        "Press **Enter** to open the selection menu or type a custom string manually."
    )
))

SCHEMA[0].append(ConfigItem(
    label="Target Mount Point",
    key="mount_point",
    scope="mount_info",
    type_="string",
    default="/",
    options=["none", "/", "/home", "/boot", "/boot/efi", "/swap", "/mnt/data"],
    hints=["Disable mounting", "Root filesystem", "User directory", "Legacy boot ESP", "UEFI ESP", "Swap space", "Generic mount data"],
    extended_help=(
        "**Target Mount Point:** The absolute directory path where the partition will be mounted.\n\n"
        "Spaces will be automatically translated to safe fstab-compliant octal codes (`\\040`)."
    )
))

# --- TAB 1: FILESYSTEM ---
SCHEMA[1].append(ConfigItem(
    label="Filesystem Type",
    key="fs_type",
    scope="filesystem",
    type_="cycle",
    default="btrfs",
    options=["btrfs", "vfat", "exfat", "ntfs", "ext4", "ext3", "ext2", "swap"],
    extended_help=(
        "**Filesystem Type:** The filesystem driver to mount with.\n\n"
        "Updated for **Arch Linux kernel 7.1**:\n"
        "- **ntfs**: Uses native, rewritten high-performance in-kernel module (replaces Paragon `ntfs3` / FUSE `ntfs-3g`).\n"
        "- **exfat**: Native exFAT driver mapping POSIX user IDs.\n"
        "- **vfat**: Standard EFI/FAT32 bootloader driver.\n"
        "- **btrfs**: Copy-on-Write multi-subvolume filesystem."
    )
))

SCHEMA[1].append(ConfigItem(
    label="Drive Architecture",
    key="drive_type",
    scope="filesystem",
    type_="cycle",
    default="ssd",
    options=["ssd", "hdd"],
    extended_help=(
        "**Drive Architecture:** SSD vs. HDD specific optimizations.\n\n"
        "Optimizations applied:\n"
        "- **SSD (Btrfs)**: Enables `ssd,discard=async` non-blocking TRIM queuing.\n"
        "- **SSD (Ext4)**: Disables toxic `discard` mount flag, utilizing `commit=20,lazytime,delalloc` (relies on fstrim.timer).\n"
        "- **HDD (Btrfs)**: Enables background `autodefrag` layout packing."
    )
))

# --- TAB 2: BTRFS OPS ---
SCHEMA[2].append(ConfigItem(
    label="BTRFS Subvolume",
    key="subvol",
    scope="btrfs_ops",
    type_="string",
    default="@",
    options=["@", "@home", "@snapshots", "@var_log", "@var_cache", "@var_tmp", "@swap"],
    hints=["Root subvolume", "Home subvolume", "Snapshots subvolume", "Log subvolume", "Cache subvolume", "Temporary subvolume", "Swap subvolume"],
    extended_help=(
        "**BTRFS Subvolume:** The specific Btrfs subvolume path to mount.\n\n"
        "Only applies when filesystem type is set to `btrfs`."
    )
))

SCHEMA[2].append(ConfigItem(
    label="Copy-on-Write (CoW)",
    key="cow_enabled",
    scope="btrfs_ops",
    type_="bool",
    default=True,
    extended_help=(
        "**Copy-on-Write (CoW):** Toggles Btrfs CoW behavior.\n\n"
        "If disabled (CoW = OFF), applies the `nodatacow` mount option (essential for virtual machine "
        "images and databases to prevent fragmentation) and disables compression."
    )
))

# --- TAB 3: SYSTEM FLAGS ---
SCHEMA[3].append(ConfigItem(
    label="Mount at Boot (auto)",
    key="auto_mount",
    scope="system_flags",
    type_="bool",
    default=True,
    extended_help=(
        "**Mount at Boot (auto):** Controls system behavior during boot.\n\n"
        "- **ON**: Passes `auto,nofail` (system mounts the drive automatically, but boots even if missing).\n"
        "- **OFF**: Passes `noauto,nofail` (system ignores the drive on boot; must be mounted manually)."
    )
))

SCHEMA[3].append(ConfigItem(
    label="Show in File Manager (GVfs)",
    key="gvfs_show",
    scope="system_flags",
    type_="bool",
    default=True,
    extended_help=(
        "**Show in File Manager (GVfs):** Toggles visibility of this mount in your file manager (like Thunar/Nautilus).\n\n"
        "- **ON**: Appends the `comment=x-gvfs-show` and `user` mount options so the drive shows in the sidebar and mounts without root prompts.\n"
        "- **OFF**: Removes the option, hiding the mountpoint from file managers."
    )
))


def DEFERRED_LOAD() -> list[int]:
    """
    Queries lsblk dynamically at startup to populate the target ID options and hints.
    """
    try:
        res = subprocess.run(
            ["lsblk", "--json", "-o", "NAME,FSTYPE,UUID,LABEL,PARTUUID,MOUNTPOINT"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL
        )
        if res.returncode == 0:
            data = json.loads(res.stdout)
            uuids = set()
            hints = {}

            def walk(device_list):
                for dev in device_list:
                    uuid = dev.get("uuid")
                    partuuid = dev.get("partuuid")
                    label = dev.get("label")
                    name = dev.get("name")
                    fstype = dev.get("fstype")
                    mp = dev.get("mountpoint")

                    details = []
                    if name: details.append(f"Name: {name}")
                    if fstype: details.append(f"Type: {fstype}")
                    if label: details.append(f"Label: {label}")
                    if mp: details.append(f"Mounted: {mp}")
                    hint_str = ", ".join(details)

                    if uuid:
                        uuids.add(uuid)
                        hints[uuid] = hint_str
                    if partuuid:
                        uuids.add(f"PARTUUID={partuuid}")
                        hints[f"PARTUUID={partuuid}"] = hint_str
                    if label:
                        uuids.add(f"LABEL={label}")
                        hints[f"LABEL={label}"] = hint_str
                    if name:
                        uuids.add(f"/dev/{name}")
                        hints[f"/dev/{name}"] = hint_str

                    if "children" in dev and dev["children"]:
                        walk(dev["children"])

            if "blockdevices" in data:
                walk(data["blockdevices"])

            sorted_uuids = sorted(list(uuids))

            uuid_item = None
            for item in SCHEMA[0]:
                if item.key == "uuid":
                    uuid_item = item
                    break

            if uuid_item:
                uuid_item.options = sorted_uuids
                uuid_item.hints = [hints.get(u, "") for u in sorted_uuids]

    except Exception as e:
        import sys
        print(f"[tui_fstab] Deferred load error: {e}", file=sys.stderr)

    return [0]

# =============================================================================
# DIRECT EXECUTION HANDLER
# =============================================================================
if __name__ == "__main__":
    import sys, subprocess
    from pathlib import Path

    script_path = Path(__file__).resolve()
    main_router = Path.home() / "user_scripts" / "dusky_tui" / "python" / "main" / "main.py"

    if main_router.exists():
        sys.exit(subprocess.run([sys.executable, str(main_router), str(script_path)] + sys.argv[1:]).returncode)
    else:
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)
