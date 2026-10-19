#!/usr/bin/env python3
"""Read SMART telemetry, map partition gaps, sample content, and discard gaps.

Linux 7.3+, Python 3.15+, util-linux 2.42+, nvme-cli 3.1+.
Sector samples describe readable bytes, not controller FTL allocation or lifespan.
Live discard is explicit and limited to verified partition-table free ranges.
"""

import argparse
lazy import json
import os
import re
lazy import shlex
lazy import shutil
lazy import stat
lazy import subprocess
import sys
lazy from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from math.integer import lcm
from typing import Any, Final, TypedDict

# --- Rich console (hard dependency) -------------------------------------------
try:
    from rich.align import Align
    from rich.columns import Columns
    from rich.console import Console, Group
    from rich.markup import escape
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:
    sys.stderr.write(
        "[!] python-rich is required for console rendering.\n"
        "    Arch Linux:  sudo pacman -S --needed python-rich\n"
    )
    sys.exit(1)

VERSION: Final[str] = "2.7.1"
console: Final[Console] = Console()
PANEL_WIDTH: Final[int] = min(console.width if console.is_terminal else 132, 132)

# --- PEP 695 type statements --------------------------------------------------
type SectorRange = tuple[int, int]
type DeviceTree = dict[str, dict[str, Any]]


class SmartData(TypedDict, total=False, closed=True):
    device: str
    model: str
    serial: str
    firmware: str
    temp: float
    percentage_used: int
    tbw_written: float
    power_on_hours: int
    unsafe_shutdowns: int
    media_errors: int
    flash_type: str
    interface: str
    health_passed: bool
    critical_warning: int
    smartctl_status: int


@dataclass(frozen=True, slots=True)
class PartitionInfo:
    name: str
    start_sector: int
    end_sector: int
    size_sectors: int
    fs_type: str
    mountpoint: str
    is_luks: bool = False
    allow_discards: bool = False
    discard_mounted: bool = False
    crypto_type: str = ""

    @property
    def is_encrypted(self) -> bool:
        return self.is_luks or bool(self.crypto_type)

    @property
    def resolved_crypto_type(self) -> str:
        if self.crypto_type:
            return self.crypto_type
        return "LUKS" if self.is_luks else ""


@dataclass(frozen=True, slots=True)
class DiskLayout:
    device: str
    model: str
    total_sectors: int
    sector_size: int
    label: str
    partitions: list[PartitionInfo]
    unallocated_gaps: list[SectorRange]
    discard_granularity: int = 0
    discard_max_bytes: int = 0


# =============================================================================
# Constants & Reference Tables
# =============================================================================
DISCARD_ALIGNMENT_BYTES: Final[int] = 4 * 1024 * 1024  # 4 MiB inward-alignment policy
DEFAULT_MAX_DISCARD_CHUNK: Final[int] = 2 * 1024 * 1024 * 1024  # 2 GiB step ceiling

QLC_PATTERNS: Final[tuple[str, ...]] = (
    "QLC", "QVO", "660P", "670P",
)

FS_COLORS: Final = frozendict({
    "btrfs": "green", "ext4": "cyan", "ext3": "cyan", "ext2": "cyan",
    "xfs": "blue", "f2fs": "magenta", "vfat": "yellow", "fat32": "yellow",
    "fat16": "yellow", "ntfs": "red", "exfat": "yellow", "bcachefs": "bright_green",
    "nilfs2": "bright_blue", "swap": "bright_red", "crypto_LUKS": "bright_magenta",
    "BitLocker": "bright_magenta", "bitlk": "bright_magenta", "zfs": "bright_cyan",
})

# =============================================================================
# Mock profiles (for --mock demonstration mode)
# =============================================================================
MOCK_INTEL_SMART: SmartData = {
    "device": "/dev/nvme0n1", "model": "INTEL SSDPEKNU512GZ (670p QLC)",
    "serial": "PHPN12345678512D", "firmware": "CO20100F", "temp": 36.0,
    "percentage_used": 30, "tbw_written": 67.51,
    "power_on_hours": 21348, "unsafe_shutdowns": 1678, "media_errors": 0,
    "flash_type": "QLC", "interface": "NVMe",
}
MOCK_INTEL_LAYOUT = DiskLayout(
    device="/dev/nvme0n1", model="INTEL SSDPEKNU512GZ (670p QLC)",
    total_sectors=1000215216, sector_size=512, label="gpt",
    partitions=[
        PartitionInfo("nvme0n1p1", 2048, 6293503, 6291456, "ext4", "/home/example", True, True, False),
        PartitionInfo("nvme0n1p2", 6293504, 9089023, 2795520, "vfat", "/boot", False, False, False),
        PartitionInfo("nvme0n1p3", 9089024, 260747263, 251658240, "btrfs", "/", False, False, True),
    ],
    unallocated_gaps=[(260747264, 1000215182)],
    discard_granularity=512, discard_max_bytes=2 * (1 << 40),
)

MOCK_SAMSUNG_SMART: SmartData = {
    "device": "/dev/nvme1n1", "model": "Samsung SSD 980 1TB (TLC)",
    "serial": "S64DNL0R123456F", "firmware": "1B4QFXO7", "temp": 40.0,
    "percentage_used": 10, "tbw_written": 81.72,
    "power_on_hours": 5539, "unsafe_shutdowns": 1478, "media_errors": 0,
    "flash_type": "TLC", "interface": "NVMe",
}
MOCK_SAMSUNG_LAYOUT = DiskLayout(
    device="/dev/nvme1n1", model="Samsung SSD 980 1TB (TLC)",
    total_sectors=1953525168, sector_size=512, label="gpt",
    partitions=[
        PartitionInfo("nvme1n1p1", 2048, 1048578047, 1048576000, "ext4", "/mnt/media", True, True, False),
    ],
    unallocated_gaps=[(1048578048, 1953525134)],
    discard_granularity=4096, discard_max_bytes=2 * (1 << 40),
)

MOCK_SATA_SMART: SmartData = {
    "device": "/dev/sda", "model": "Samsung SSD 870 QVO 2TB (QLC)",
    "serial": "S5XANGB1234567W", "firmware": "1B6QJX7", "temp": 38.0,
    "percentage_used": 15, "tbw_written": 108.5,
    "power_on_hours": 8760, "unsafe_shutdowns": 42, "media_errors": 0,
    "flash_type": "QLC", "interface": "SATA",
}
MOCK_SATA_LAYOUT = DiskLayout(
    device="/dev/sda", model="Samsung SSD 870 QVO 2TB (QLC)",
    total_sectors=3907029168, sector_size=512, label="gpt",
    partitions=[
        PartitionInfo("sda1", 2048, 1050623, 1048576, "vfat", "/boot", False, False, True),
        PartitionInfo("sda2", 1050624, 2095103, 1044480, "swap", "[SWAP]", False, False, False),
        PartitionInfo("sda3", 2095104, 3906961407, 3904866304, "ext4", "/mnt/data", True, True, True),
    ],
    unallocated_gaps=[(3906961408, 3907029134)],
    discard_granularity=512, discard_max_bytes=2 * (1 << 30),
)


# =============================================================================
# Subprocess & IO Helpers
# =============================================================================
def _run(args: list[str], timeout: float | None = 5.0) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout,
                              env={**os.environ, "LC_ALL": "C", "LIBSMARTCOLS_JSON": "compact",
                                   "LIBSMARTCOLS_DEBUG_PADDING": "off"})
    except (subprocess.TimeoutExpired, OSError):
        return None


def _run_json(args: list[str], timeout: float = 5.0, *, smartctl: bool = False) -> dict[str, Any] | None:
    """SMART health bits may be nonzero; invocation/open failures are rejected."""
    res = _run(args, timeout=timeout)
    if res is None or res.returncode < 0 or (res.returncode & 3 if smartctl else res.returncode != 0):
        return None
    try:
        data = json.loads(res.stdout)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


# =============================================================================
# Hardware Helper Calculations
# =============================================================================
def is_rotational(device: str, is_mock: bool = False) -> bool:
    if is_mock:
        return False
    content = _read_text(f"/sys/class/block/{os.path.basename(os.path.realpath(device))}/queue/rotational")
    # Unknown capability must not enable a destructive flash operation.
    return content is None or content.strip() != "0"


def detect_flash_type(model: str, is_hdd: bool = False) -> str:
    if is_hdd:
        return "Magnetic Platter (HDD)"
    model = model.upper()
    if any(pattern in model for pattern in QLC_PATTERNS):
        return "QLC (model inference)"
    if "TLC" in model:
        return "TLC (model inference)"
    return "Unknown"


def _get_device_sector_size(device: str) -> int:
    if content := _read_text(f"/sys/class/block/{os.path.basename(device)}/queue/logical_block_size"):
        with suppress(ValueError):
            return int(content.strip())
    return 0


def _get_device_discard_granularity(device: str) -> int:
    if content := _read_text(f"/sys/class/block/{os.path.basename(device)}/queue/discard_granularity"):
        with suppress(ValueError):
            return int(content.strip())
    return 0


def _get_device_discard_max_bytes(device: str) -> int:
    if content := _read_text(f"/sys/class/block/{os.path.basename(device)}/queue/discard_max_bytes"):
        with suppress(ValueError):
            return int(content.strip())
    return 0


def _get_device_model(device: str) -> str:
    if content := _read_text(f"/sys/class/block/{os.path.basename(device)}/device/model"):
        if model_str := content.strip():
            return model_str

    if res := _run(["lsblk", "-d", "-o", "MODEL", device, "--noheadings"], timeout=3.0):
        if res.returncode == 0 and res.stdout:
            return res.stdout.strip()
    return "Unknown Device"


# =============================================================================
# Device Topology & Partition Parsing
# =============================================================================
def detect_ssd_devices() -> list[str]:
    devices: list[str] = []
    data = _run_json(["lsblk", "-d", "-J", "-o", "NAME,ROTA,TYPE"])
    if data is None:
        return devices
    entries = data.get("blockdevices")
    if not isinstance(entries, list):
        return devices

    for d in entries:
        if not isinstance(d, dict):
            continue
        name = d.get("name")
        if not isinstance(name, str) or d.get("type") != "disk" or not name.startswith(("nvme", "sd")):
            continue
        rota = str(d.get("rota", 1)).lower()
        if rota not in ("0", "false"):
            continue
        if content := _read_text(f"/sys/class/block/{name}/queue/rotational"):
            if content.strip() != "0":
                continue
        devices.append(f"/dev/{name}")
    return sorted(devices)


def get_mount_discards() -> dict[str, bool]:
    """Match mounts by device number and source, including Btrfs pseudo devices."""
    discards: dict[str, bool] = {}
    content = _read_text("/proc/self/mountinfo")
    if content is None:
        return discards
    for line in content.splitlines():
        fields = line.split()
        try:
            separator = fields.index("-")
        except ValueError:
            continue
        if separator < 6 or len(fields) <= separator + 3:
            continue
        source = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), fields[separator + 2])
        options = fields[5].split(",") + fields[separator + 3].split(",")
        enabled = any(option == "discard" or option.startswith("discard=") and option != "discard=none"
                      for option in options)
        keys = [fields[2], source]
        if source.startswith("/dev/"):
            keys.append(os.path.realpath(source))
        for key in keys:
            discards[key] = discards.get(key, False) or enabled
    return discards


def build_device_tree(device: str) -> DeviceTree:
    result: DeviceTree = {}
    data = _run_json(["lsblk", "-J", "-b", "-o", "NAME,TYPE,FSTYPE,MOUNTPOINTS,MAJ:MIN,PKNAME,DISC-GRAN", device])
    if data is None or not isinstance(data, dict):
        return result

    def walk(node: dict[str, Any]) -> None:
        name = node.get("name", "")
        if name:
            result[name] = {
                "type": node.get("type", ""),
                "fstype": node.get("fstype") or "",
                "mountpoints": [mp for mp in (node.get("mountpoints") or []) if mp],
                "maj_min": node.get("maj:min", ""),
                "pkname": node.get("pkname") or "",
                "disc_gran": int(node.get("disc-gran") or 0),
                "children": [c.get("name", "") for c in (node.get("children") or [])],
            }
        for child in node.get("children") or []:
            walk(child)

    for dev in data.get("blockdevices", []):
        walk(dev)
    return result


def resolve_mountpoints(part_name: str, tree: DeviceTree) -> list[str]:
    seen: dict[str, None] = {}
    visited: set[str] = set()

    def walk(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        node = tree.get(name, {})
        for mp in node.get("mountpoints", []):
            if mp:
                seen[mp] = None
        for child in node.get("children", []):
            walk(child)

    walk(part_name)
    return list(seen)


def analyze_partition_discard(part_name: str, tree: DeviceTree, mount_discards: dict[str, bool]) -> tuple[bool, bool, str]:
    crypto_types: set[str] = set()
    crypt_support: list[bool] = []
    discard_mounted = False
    visited: set[str] = set()

    def walk(name: str) -> None:
        nonlocal discard_mounted
        if name in visited:
            return
        visited.add(name)
        node = tree.get(name, {})
        fstype = str(node.get("fstype") or "").lower()
        if fstype == "crypto_luks":
            crypto_types.add("LUKS")
        elif fstype in {"bitlocker", "bitlk"}:
            crypto_types.add("BitLocker")
        if node.get("type") == "crypt":
            crypt_support.append(node.get("disc_gran", 0) > 0)
            if not crypto_types:
                crypto_types.add("Crypt")
        keys = (node.get("maj_min", ""), f"/dev/mapper/{name}", f"/dev/{name}")
        discard_mounted |= any(mount_discards.get(key, False) for key in keys)
        for child in node.get("children", []):
            walk(child)

    walk(part_name)
    crypto_type = "/".join(sorted(crypto_types))
    return bool(crypt_support) and all(crypt_support), discard_mounted, crypto_type


def _compute_gaps_from_json(pt_json: dict[str, Any], total_sectors: int, sector_size: int = 512) -> list[SectorRange]:
    """GPT usable LBAs exclude primary/backup headers and partition arrays."""
    pt = pt_json["partitiontable"]
    if pt.get("label") != "gpt" or pt.get("unit") != "sectors":
        raise ValueError("GPT with sector units is required for JSON gap computation")
    if int(pt.get("sectorsize", 0)) != sector_size:
        raise ValueError("Partition table and device sector sizes differ")
    first, last = int(pt["firstlba"]), int(pt["lastlba"])
    if not 0 < first <= last < total_sectors:
        raise ValueError("Invalid usable LBA bounds")
    minimum = (1024 * 1024 + sector_size - 1) // sector_size
    gaps: list[SectorRange] = []
    cursor = first
    for part in sorted(pt.get("partitions", []), key=lambda part: int(part["start"])):
        start, size = int(part["start"]), int(part["size"])
        end = start + size - 1
        if size <= 0 or start < cursor or end > last:
            raise ValueError("Overlapping or out-of-bounds partition")
        if start - cursor >= minimum:
            gaps.append((cursor, start - 1))
        cursor = end + 1
    if last - cursor + 1 >= minimum:
        gaps.append((cursor, last))
    return gaps


def parse_partition_table(device: str) -> DiskLayout | None:
    """Treat malformed command metadata as an unavailable layout, not a crash."""
    try:
        return _parse_partition_table(device)
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as error:
        console.print(Text(f"Cannot parse device layout for {device}: {error}"))
        return None


def _parse_partition_table(device: str) -> DiskLayout | None:
    if not shutil.which("sfdisk"):
        console.print("[red]sfdisk not found. Please install util-linux.[/]")
        return None

    dev_name = os.path.basename(device)
    sector_size = _get_device_sector_size(device)
    total_sectors = 0
    if sector_size <= 0:
        console.print(f"[red]Could not determine logical sector size for {device}[/]")
        return None

    if content := _read_text(f"/sys/class/block/{dev_name}/size"):
        with suppress(ValueError):
            total_sectors = (int(content.strip()) * 512) // sector_size

    if total_sectors <= 0:
        console.print(f"[red]Could not determine sector count for {device}[/]")
        return None

    model = _get_device_model(device)
    disc_gran = _get_device_discard_granularity(device)
    disc_max = _get_device_discard_max_bytes(device)

    pt_data = _run_json(["sfdisk", "--json", device])

    if pt_data is None or not pt_data.get("partitiontable"):
        console.print(f"[yellow]No readable partition table on {device}; no free extents will be assumed.[/]")
        tree = build_device_tree(device)
        root = tree.get(dev_name, {})
        fstype = root.get("fstype", "")
        if fstype:
            mps = list(dict.fromkeys(root.get("mountpoints", [])))
            allow_d, disc_m, crypto_type = analyze_partition_discard(dev_name, tree, get_mount_discards())
            partitions = [
                PartitionInfo(
                    name=dev_name, start_sector=0, end_sector=total_sectors - 1,
                    size_sectors=total_sectors, fs_type=fstype,
                    mountpoint=", ".join(mps) if mps else "unmounted",
                    is_luks=(crypto_type == "LUKS"), crypto_type=crypto_type,
                    allow_discards=allow_d, discard_mounted=disc_m,
                )
            ]
            return DiskLayout(
                device=device, model=model, total_sectors=total_sectors,
                sector_size=sector_size, label="none", partitions=partitions,
                unallocated_gaps=[], discard_granularity=disc_gran, discard_max_bytes=disc_max,
            )
        return DiskLayout(
            device=device, model=model, total_sectors=total_sectors,
            sector_size=sector_size, label="none", partitions=[],
            unallocated_gaps=[], discard_granularity=disc_gran, discard_max_bytes=disc_max,
        )

    pt = pt_data["partitiontable"]
    try:
        if pt.get("label") == "gpt":
            gaps = _compute_gaps_from_json(pt_data, total_sectors, sector_size)
        elif pt.get("label") == "dos" and pt.get("unit") == "sectors" and int(pt.get("sectorsize", 0)) == sector_size:
            # libfdisk knows EBR reservations; subtracting MBR entries does not.
            res = _run(["sfdisk", "--list-free", "--color=never", device])
            if res is None or res.returncode != 0:
                raise ValueError("Cannot read MBR free extents")
            gaps = []
            minimum = (1024 * 1024 + sector_size - 1) // sector_size
            for line in res.stdout.splitlines():
                fields = line.split()
                if len(fields) >= 3 and all(field.isdecimal() for field in fields[:3]):
                    start, end, count = map(int, fields[:3])
                    if not 0 < start <= end < total_sectors or count != end - start + 1:
                        raise ValueError("Invalid MBR free extent")
                    if count >= minimum:
                        gaps.append((start, end))
        else:
            raise ValueError("Unsupported partition-table format")
    except (KeyError, TypeError, ValueError) as error:
        console.print(f"[red]Cannot establish free ranges for {device}: {error}[/]")
        return None

    tree = build_device_tree(device)
    mount_discards = get_mount_discards()
    partitions: list[PartitionInfo] = []

    for part in pt.get("partitions", []):
        name = part.get("node", "").split("/")[-1]
        start, size = int(part["start"]), int(part["size"])

        if start < 0 or size <= 0 or start + size > total_sectors:
            console.print(f"[red]Invalid partition extent on {device}[/]")
            return None

        node = tree.get(name, {})
        fs_type = node.get("fstype") or "unknown"
        mps = resolve_mountpoints(name, tree)
        mp = "[SWAP]" if not mps and fs_type == "swap" else (", ".join(mps) if mps else "unmounted")
        allow_discards, discard_mounted, crypto_type = analyze_partition_discard(name, tree, mount_discards)

        partitions.append(PartitionInfo(
            name=name, start_sector=start, end_sector=start + size - 1, size_sectors=size,
            fs_type=fs_type, mountpoint=mp, is_luks=(crypto_type == "LUKS"),
            crypto_type=crypto_type,
            allow_discards=allow_discards, discard_mounted=discard_mounted,
        ))

    # Reserve extended containers too: their free areas can contain EBR metadata,
    # and the kernel exposes the whole container as an occupied partition range.
    if pt.get("label") == "dos":
        external_gaps = [(start, end) for start, end in gaps
                         if not any(start <= part.end_sector and end >= part.start_sector
                                    for part in partitions)]
        if len(external_gaps) != len(gaps):
            console.print("[dim]Free areas inside MBR extended containers are reserved; omitted from sampling and discard.[/]")
        gaps = external_gaps

    return DiskLayout(
        device=device, model=model, total_sectors=total_sectors,
        sector_size=sector_size, label=pt.get("label", "unknown"),
        partitions=partitions, unallocated_gaps=gaps,
        discard_granularity=disc_gran, discard_max_bytes=disc_max,
    )


# =============================================================================
# SMART Telemetry
# =============================================================================
def _nvme_smart_log(ctrl: str) -> dict[str, Any] | None:
    """Read controller-wide SMART counters, even when opened via a namespace."""
    return _run_json(["nvme", "log", "smart", ctrl, "-o", "json", "--output-format-version=2"], timeout=10.0)


def _nvme_id_ctrl(ctrl: str) -> dict[str, Any] | None:
    return _run_json(["nvme", "id", "ctrl", ctrl, "-o", "json", "--output-format-version=2"], timeout=10.0)


def _query_nvme_smart(device: str) -> SmartData | None:
    ctrl = device if re.fullmatch(r"nvme\d+(?:c\d+)?n\d+", os.path.basename(device)) else None
    if not ctrl or not shutil.which("nvme"):
        return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        log_future = pool.submit(_nvme_smart_log, ctrl)
        id_future = pool.submit(_nvme_id_ctrl, ctrl)
        log = log_future.result()
        ident = id_future.result()

    if not log or not isinstance(log, dict):
        return None

    smart: SmartData = {"device": device, "interface": "NVMe"}

    if log.get("temperature") is not None:
        kelvin = int(log["temperature"])
        if kelvin > 0:
            smart["temp"] = round(kelvin - 273.15, 2)

    if "percent_used" in log:
        smart["percentage_used"] = int(log["percent_used"])
    for key in ("critical_warning", "power_on_hours", "unsafe_shutdowns", "media_errors"):
        if key in log:
            smart[key] = int(log[key])
    if "data_units_written" in log:
        smart["tbw_written"] = round(int(log["data_units_written"]) * 512_000 / 1e12, 2)

    if ident and isinstance(ident, dict):
        smart["model"] = str(ident.get("mn") or "Unknown NVMe").strip()
        smart["serial"] = str(ident.get("sn") or "N/A").strip()
        smart["firmware"] = str(ident.get("fr") or "N/A").strip()
    else:
        smart["model"] = _get_device_model(device)
        smart["serial"], smart["firmware"] = "N/A", "N/A"

    smart["flash_type"] = detect_flash_type(smart["model"])
    return smart


def _extract_sata_wear_percentage(attrs: dict[int, dict[str, Any]]) -> int | None:
    # Only use names explicitly decoded by the smartmontools drive database.
    for attr in attrs.values():
        name = str(attr.get("name", "")).lower()
        if name in {"percent_lifetime_remain", "ssd_life_left", "media_wearout_indicator"}:
            value = attr.get("value")
            if isinstance(value, int) and 0 <= value <= 100:
                return 100 - value
        if name == "percent_lifetime_used":
            value = (attr.get("raw") or {}).get("value")
            if isinstance(value, int) and value >= 0:
                return value
    return None


def _extract_sata_tbw(attrs: dict[int, dict[str, Any]]) -> float | None:
    for attr in attrs.values():
        name = str(attr.get("name", "")).upper()
        unit = {"TOTAL_LBAS_WRITTEN": 512, "HOST_WRITES_32MIB": 1 << 25,
                "HOST_WRITES_32MB": 1 << 25, "HOST_WRITES_GIB": 1 << 30}.get(name)
        value = (attr.get("raw") or {}).get("value")
        if unit is not None and isinstance(value, int) and value >= 0:
            return round(value * unit / 1e12, 2)
    return None


def _query_block_smart(device: str) -> SmartData | None:
    if not shutil.which("smartctl"):
        console.print("[red]smartctl not found. Please install smartmontools.[/]")
        return None
    for args in (["smartctl", "-x", "--json", device], ["smartctl", "-x", "--json", "-d", "sat", device]):
        data = _run_json(args, timeout=10.0, smartctl=True)
        if not data or not any(key in data for key in ("ata_smart_attributes", "nvme_smart_health_information_log", "smart_status")):
            continue
        protocol = str((data.get("device") or {}).get("protocol", "Unknown"))
        hdd = is_rotational(device)
        smart: SmartData = {
            "device": device, "interface": f"{protocol} (Rotational HDD)" if hdd else protocol,
            "model": str(data.get("model_name") or data.get("scsi_model_name") or _get_device_model(device)),
            "serial": str(data.get("serial_number") or "Unknown"),
            "firmware": str(data.get("firmware_version") or "Unknown"),
        }
        smart["smartctl_status"] = int((data.get("smartctl") or {}).get("exit_status", 0))
        passed = (data.get("smart_status") or {}).get("passed")
        if isinstance(passed, bool):
            smart["health_passed"] = passed
        temp = (data.get("temperature") or {}).get("current")
        if temp is not None:
            smart["temp"] = float(temp)
        hours = (data.get("power_on_time") or {}).get("hours")
        if hours is not None:
            smart["power_on_hours"] = int(hours)
        if nvme := data.get("nvme_smart_health_information_log"):
            for key in ("percentage_used", "power_on_hours", "unsafe_shutdowns", "media_errors", "critical_warning"):
                if key in nvme:
                    smart[key] = int(nvme[key])
            if "data_units_written" in nvme:
                smart["tbw_written"] = round(int(nvme["data_units_written"]) * 512_000 / 1e12, 2)
        elif ata := data.get("ata_smart_attributes"):
            attrs = {attr["id"]: attr for attr in ata.get("table", []) if "id" in attr}
            wear = None if hdd else _extract_sata_wear_percentage(attrs)
            if wear is not None:
                smart["percentage_used"] = wear
            writes = _extract_sata_tbw(attrs)
            if writes is not None:
                smart["tbw_written"] = writes
            for attr in attrs.values():
                if attr.get("name") == "Unexpected_Power_Loss":
                    raw = (attr.get("raw") or {}).get("value")
                    if raw is not None:
                        smart["unsafe_shutdowns"] = int(raw)
            error_names = {"Reallocated_Sector_Ct", "Reported_Uncorrect", "Current_Pending_Sector", "Offline_Uncorrectable"}
            errors = [(attr.get("raw") or {}).get("value") for attr in attrs.values() if attr.get("name") in error_names]
            if errors and all(isinstance(value, int) for value in errors):
                smart["media_errors"] = sum(errors)
        elif "scsi_grown_defect_list" in data:
            smart["media_errors"] = int(data["scsi_grown_defect_list"])
        smart["flash_type"] = detect_flash_type(smart["model"], is_hdd=hdd)
        return smart
    return None


def query_live_smart_data(device: str) -> SmartData | None:
    if re.fullmatch(r"nvme\d+(?:c\d+)?n\d+", os.path.basename(device)):
        try:
            if smart := _query_nvme_smart(device):
                return smart
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as error:
            console.print(Text(f"NVMe telemetry parsing failed for {device}: {error}; trying smartctl."))
        return _query_block_smart(device)
    return _query_block_smart(device)


# =============================================================================
# Sector Content Sampling
# =============================================================================
def scan_unallocated_regions(dev_path: str, gaps: list[SectorRange], sector_size: int = 512, total_samples: int = 500) -> float | None:
    """Return the sampled nonzero/non-FF fraction; I/O failures mean unknown."""
    if sector_size <= 0 or total_samples <= 0:
        raise ValueError("Sector size and sample budget must be positive")
    valid = [(start, end) for start, end in gaps if 0 <= start <= end]
    total = sum(end - start + 1 for start, end in valid)
    if not total:
        return None
    samples = min(total_samples, total)
    dirty = 0
    zero_block, ff_block = bytes(4096), b"\xff" * 4096
    try:
        fd = os.open(dev_path, os.O_RDONLY | os.O_CLOEXEC)
        try:
            gap_index = 0
            base = 0
            for index in range(samples):
                # Stratified midpoint samples over the union, weighted by gap size.
                position = (2 * index + 1) * total // (2 * samples)
                start, end = valid[gap_index]
                while position >= base + end - start + 1:
                    base += end - start + 1
                    gap_index += 1
                    start, end = valid[gap_index]
                sector = start + position - base
                length = min(4096, (end - sector + 1) * sector_size)
                block = os.pread(fd, length, sector * sector_size)
                if len(block) != length:
                    raise OSError(f"Short read at sector {sector}: {len(block)}/{length} bytes")
                dirty += not (zero_block.startswith(block) or ff_block.startswith(block))
        finally:
            os.close(fd)
    except OSError as error:
        console.print(f"[red]Sector scan failed for {dev_path}: {error}[/]")
        return None
    console.print(f"Sampled {samples} blocks: {dirty} contain bytes other than all zero/all FF.")
    return dirty / samples


# =============================================================================
# Host System & Queue Telemetry
# =============================================================================
def _format_bytes(bytes_val: int) -> str:
    if bytes_val <= 0:
        return "[dim]0 B (No hardware support)[/]"
    for unit, threshold in (("TiB", 1 << 40), ("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)):
        if bytes_val >= threshold:
            return f"[bold bright_cyan]{bytes_val / threshold:.1f} {unit}[/]"
    return f"[bold bright_cyan]{bytes_val} B[/]"


def _query_storage_driver(device: str) -> str:
    try:
        device_path = os.path.realpath(f"/sys/class/block/{os.path.basename(device)}/device")
        for _ in range(8):
            driver_link = os.path.join(device_path, "driver")
            if os.path.islink(driver_link):
                driver_name = os.path.basename(os.readlink(driver_link))
                if driver_name not in ("sd", "sr", "scsi"):
                    return f"Active ({driver_name} controller driver)"
            parent = os.path.dirname(device_path)
            if parent == device_path or parent == "/":
                break
            device_path = parent
    except OSError:
        pass
    return "Unknown (no controller driver resolved)"


def query_system_telemetry(device: str, is_mock: bool = False, is_nvme: bool = True) -> dict[str, str]:
    telemetry: dict[str, str] = {
        "kernel": "7.3.0 (mock)" if is_mock else "Unknown",
        "fstrim_timer": "Active (Weekly)" if is_mock else "Unknown",
        "discard_granularity": "[bold bright_cyan]512 B[/]" if is_mock else "N/A",
        "discard_max_bytes": "[bold bright_cyan]2.0 TiB[/]" if is_mock else "N/A",
        "storage_driver": "Active (nvme controller driver, mock)" if is_mock and is_nvme else "Active (ahci SATA HBA driver)" if is_mock else "Unknown"
    }

    if is_mock:
        if is_nvme and "nvme1n1" in device:
            telemetry["discard_granularity"] = "[bold bright_cyan]4.0 KiB[/]"
        if not is_nvme:
            telemetry["discard_max_bytes"] = "[bold bright_cyan]2.0 GiB[/]"
        return telemetry

    telemetry["kernel"] = os.uname().release

    if res := _run(["systemctl", "is-active", "fstrim.timer"], timeout=3.0):
        telemetry["fstrim_timer"] = "Active (System Timer)" if res.returncode == 0 else res.stdout.strip().capitalize() or "Unknown"

    dev_name = os.path.basename(device)
    for key, path_suffix in (("discard_granularity", "queue/discard_granularity"), ("discard_max_bytes", "queue/discard_max_bytes")):
        if content := _read_text(f"/sys/class/block/{dev_name}/{path_suffix}"):
            with suppress(ValueError):
                val = int(content.strip())
                telemetry[key] = _format_bytes(val) if key == "discard_max_bytes" or val > 0 else "0 (No discard support)"

    telemetry["storage_driver"] = _query_storage_driver(device)

    return telemetry


# =============================================================================
# Dashboard Rendering & Discard Alignment Logic
# =============================================================================
def draw_layout_bar(layout: DiskLayout, dirty_ratio: float | None) -> str:
    width = 64
    bar = ["[dim grey]─[/]"] * width
    total = layout.total_sectors

    def scale(start: int, end: int) -> tuple[int, int]:
        return max(0, min(width - 1, int((start / total) * width))), max(0, min(width - 1, int((end / total) * width)))

    for gap in layout.unallocated_gaps:
        s, e = scale(gap[0], gap[1])
        if dirty_ratio is None:
            for index in range(s, e + 1):
                bar[index] = "[dim]░[/]"
            continue
        dirty_chars = int((e - s + 1) * dirty_ratio)
        for idx in range(s, s + dirty_chars):
            if 0 <= idx < width:
                bar[idx] = "[bold yellow]░[/]"
        for idx in range(s + dirty_chars, e + 1):
            if 0 <= idx < width:
                bar[idx] = "[bold green]▒[/]"

    for part in layout.partitions:
        s, e = scale(part.start_sector, part.end_sector)
        color = FS_COLORS.get(part.fs_type, "cyan")
        for idx in range(s, e + 1):
            if 0 <= idx < width:
                bar[idx] = f"[bold {color}]█[/]"

    return "".join(bar)


def align_gap_to_erase_blocks(gap: SectorRange, sector_size: int = 512, disc_granularity: int = 0) -> SectorRange | None:
    """Inward alignment; 4 MiB is a policy margin, not a known erase size."""
    if sector_size <= 0 or gap[0] < 0 or gap[1] < gap[0]:
        return None
    alignment = lcm(sector_size, max(1, disc_granularity), DISCARD_ALIGNMENT_BYTES)
    start = (gap[0] * sector_size + alignment - 1) // alignment * alignment
    end = (gap[1] + 1) * sector_size // alignment * alignment
    return (start // sector_size, end // sector_size - 1) if end > start else None


def _discard_plan(layout: DiskLayout, force: bool = False, is_mock: bool = False) -> list[list[str]]:
    if layout.sector_size <= 0 or layout.total_sectors <= 0:
        raise ValueError("Disk capacity and sector size must be positive")
    if is_rotational(layout.device, is_mock=is_mock) or layout.discard_max_bytes <= 0:
        return []
    # A bounded --step keeps a single process per extent; the kernel also splits I/O.
    step = min(layout.discard_max_bytes, DEFAULT_MAX_DISCARD_CHUNK)
    step = step // layout.sector_size * layout.sector_size
    if step <= 0:
        return []
    commands = []
    previous_end = -1
    for gap in sorted(layout.unallocated_gaps):
        if not previous_end < gap[0] <= gap[1] < layout.total_sectors:
            raise ValueError("Invalid, overlapping or out-of-bounds free extents")
        previous_end = gap[1]
        aligned = align_gap_to_erase_blocks(gap, layout.sector_size, layout.discard_granularity)
        if aligned is None:
            continue
        start, end = aligned
        if end >= layout.total_sectors or any(start <= part.end_sector and end >= part.start_sector for part in layout.partitions):
            raise ValueError("Discard extent overlaps a partition or exceeds the disk")
        args = ["blkdiscard"]
        if force:
            args.append("--force")
        args.extend(["--offset", str(start * layout.sector_size), "--length", str((end - start + 1) * layout.sector_size),
                     "--step", str(step), layout.device])
        commands.append(args)
    return commands


def _build_discard_commands(layout: DiskLayout, force: bool = False, is_mock: bool = False) -> list[str]:
    return [shlex.join(["sudo", *args]) for args in _discard_plan(layout, force, is_mock)]


def execute_discards(layout: DiskLayout) -> bool:
    """Report success only after every planned extent succeeds."""
    current = parse_partition_table(layout.device)
    if current != layout:
        console.print("[red]Device layout changed or could not be revalidated; discard stopped.[/]")
        return False
    try:
        commands = _discard_plan(layout, force=True)
    except ValueError as error:
        console.print(Text(f"Discard plan rejected: {error}"))
        return False
    # The kernel can retain old partition bounds after an on-disk table change.
    sys_disk = f"/sys/class/block/{os.path.basename(layout.device)}"
    try:
        with os.scandir(sys_disk) as entries:
            partition_paths = [entry.path for entry in entries if os.path.isfile(f"{entry.path}/partition")]
        for path in partition_paths:
            start = int(_read_text(f"{path}/start") or "-1") * 512
            size = int(_read_text(f"{path}/size") or "0") * 512
            if start < 0 or size <= 0:
                raise ValueError("Cannot read kernel partition bounds")
            for args in commands:
                offset = int(args[args.index("--offset") + 1])
                length = int(args[args.index("--length") + 1])
                if offset < start + size and offset + length > start:
                    raise ValueError("Planned discard overlaps a kernel partition")
    except (OSError, ValueError) as error:
        console.print(Text(f"Discard plan rejected: {error}"))
        return False
    if not commands:
        console.print("[yellow]No supported, aligned free extents to discard.[/]")
        return False
    for args in commands:
        console.print(Text(shlex.join(args)))
        # Whole extents on large/slow drives can legitimately take minutes.
        result = _run(args, timeout=None)
        if result is None or result.returncode != 0:
            detail = "command could not start" if result is None else result.stderr.strip() or f"exit status {result.returncode}"
            console.print(Text(f"Discard failed: {detail}. Earlier extents may have completed."))
            return False
    console.print("[green]All planned discard extents completed successfully; alignment margins remain untouched.[/]")
    return True


def _health_text(smart: SmartData) -> str:
    if smart.get("health_passed") is False or smart.get("critical_warning", 0) or smart.get("smartctl_status", 0) & 24:
        return "[bold red]SMART warning/failure[/]"
    if smart.get("smartctl_status", 0) & 4:
        return "[yellow]SMART command/checksum error (partial telemetry)[/]"
    if smart.get("smartctl_status", 0) & 224:
        return "[yellow]SMART history / error / self-test log warning[/]"
    if smart.get("media_errors", 0) > 0:
        return "[yellow]Media error counters nonzero (warning)[/]"
    wear = smart.get("percentage_used")
    if wear is not None:
        remaining = max(0, min(100, 100 - wear))
        color = "red" if remaining < 25 else "yellow" if remaining < 90 else "green"
        return f"[{color}]{remaining}% endurance remaining[/]"
    return "[green]SMART passed[/]" if smart.get("health_passed") is True else "[yellow]Unknown[/]"


def _build_smart_table(smart: SmartData) -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column("Key", style="dim", width=23)
    table.add_column("Value", style="bold")
    for label, key in [("Model:", "model"), ("Serial:", "serial"), ("Firmware:", "firmware"),
                       ("Interface:", "interface"), ("Flash Type:", "flash_type")]:
        table.add_row(label, Text(str(smart.get(key, "Unknown"))))
    table.add_row("SMART / Endurance:", _health_text(smart))
    for label, key, suffix in [("Temperature:", "temp", " °C"), ("Host Writes:", "tbw_written", " TB"),
                               ("Power On Hours:", "power_on_hours", " h"), ("Unsafe Shutdowns:", "unsafe_shutdowns", ""),
                               ("Media Error Counters:", "media_errors", ""),
                               ("NVMe Critical Warning:", "critical_warning", ""), ("smartctl Status Bitmask:", "smartctl_status", "")]:
        value = smart.get(key)
        table.add_row(label, "Unknown" if value is None else f"{value:,}{suffix}")
    return table


def _build_space_table(layout: DiskLayout, scan_ratio: float | None, is_cleared: bool = False) -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column("Key", style="dim", width=29)
    table.add_column("Value", style="bold")
    free = sum(end - start + 1 for start, end in layout.unallocated_gaps)
    for label, sectors in [("Disk Capacity:", layout.total_sectors), ("Unallocated Extents (≥1 MiB):", free)]:
        table.add_row(label, f"{sectors * layout.sector_size / (1 << 30):.2f} GiB ({sectors:,} sectors)")
    table.add_row("Partition Table:", layout.label)
    table.add_row("Unallocated Ratio:", "Unknown" if layout.label == "none" else f"{free / layout.total_sectors * 100:.2f}%")
    table.add_row("Discard Result:", "[green]Planned extents completed[/]" if is_cleared else "Not executed / not completed")
    table.add_row("Sampled Nonzero / Non-FF:", "Unknown / not sampled" if scan_ratio is None else f"{scan_ratio:.2%} of sampled blocks")
    table.add_row("Controller FTL / WAF:", "Not measurable from sector content")
    return table


def _build_partition_table(layout: DiskLayout, is_mock: bool = False) -> Table:
    hdd = is_rotational(layout.device, is_mock=is_mock)
    table = Table(
        title="Partition Discard & Encryption Configuration",
        header_style="bold cyan",
        border_style="dim",
        show_lines=False,
        expand=True,
        width=PANEL_WIDTH,
    )
    for col, st, rt, ju in [
        ("Partition", "bold green", 1, "left"),
        ("Type", "blue", 1, "left"),
        ("Mountpoint", "white", 2, "left"),
        ("Encrypted?", "magenta", 1, "center"),
        ("Crypto Discard Passthrough", "yellow", 2, "center"),
        ("FS Mount Discard Flag", "cyan", 2, "center"),
    ]:
        table.add_column(col, style=st, ratio=rt, justify=ju)

    for p in layout.partitions:
        crypt = p.resolved_crypto_type
        if crypt == "LUKS":
            enc_str = "[bold magenta]LUKS[/]"
        elif crypt == "BitLocker":
            enc_str = "[bold magenta]BitLocker[/]"
        elif crypt:
            enc_str = f"[bold magenta]{crypt}[/]"
        else:
            enc_str = "No"

        if hdd:
            crypto_pt = "[dim]N/A (Rotational Media)[/]"
            fs_discard = "[dim]N/A (Rotational Media)[/]"
        else:
            if p.is_encrypted:
                crypto_pt = "[bold green]Available on all active crypt maps[/]" if p.allow_discards else "[bold yellow]Unavailable / blocked[/]"
            else:
                crypto_pt = "[dim]N/A (No Encryption)[/]"

            if p.mountpoint in ("unmounted", "[SWAP]"):
                fs_discard = "[dim]N/A (Unmounted/Swap)[/]"
            elif p.discard_mounted:
                fs_discard = "[bold green]Active on a mounted descendant[/]"
            else:
                fs_discard = "[bold yellow]Inactive (No discard flag)[/]"

        table.add_row(Text(p.name), Text(p.fs_type), Text(p.mountpoint), enc_str, crypto_pt, fs_discard)
    return table


def render_drive_diagnostics(layout: DiskLayout, smart: SmartData, scan_ratio: float | None, dry_run: bool = False, exec_discard: bool = False, is_mock: bool = False) -> None:
    hdd = is_rotational(layout.device, is_mock=is_mock)
    health_str = _health_text(smart)

    sys_tel = query_system_telemetry(layout.device, is_mock=is_mock, is_nvme=(smart.get("interface", "NVMe") == "NVMe"))
    sys_table = Table.grid(padding=(0, 2))
    sys_table.add_column("Key", style="dim", width=30)
    sys_table.add_column("Value", style="bold")
    drv = sys_tel.get("storage_driver", "N/A")
    for k, v in [("Arch Linux Kernel Version:", f"[bold bright_blue]{sys_tel.get('kernel', 'N/A')}[/]"), ("Systemd TRIM Service Timer:", f"[bold green]{sys_tel.get('fstrim_timer', 'N/A')}[/]" if "Active" in sys_tel.get("fstrim_timer", "") else f"[bold yellow]{sys_tel.get('fstrim_timer', 'N/A')}[/]"), ("Device Discard Granularity:", sys_tel.get("discard_granularity", "N/A")), ("Device Max Discard Block Size:", sys_tel.get("discard_max_bytes", "N/A")), ("Active Storage Driver:", f"[bold green]{drv}[/]" if "Active" in drv else drv)]:
        sys_table.add_row(k, v)

    sys_panel = Panel(sys_table, title="[bold white]Host OS & Storage Queue Telemetry[/]", border_style="dim", width=PANEL_WIDTH)
    legend = "[bold cyan]█[/] Ext4/Btrfs    [bold bright_magenta]█[/] LUKS Map    [bold bright_red]█[/] Swap    [bold yellow]░[/] Nonzero/Non-FF Samples    [bold green]▒[/] Zero/FF Samples    [dim grey]─[/] Slack    [dim]░[/] Unknown / unsampled"

    visual_ratio = None if exec_discard or hdd else scan_ratio

    group = Group(
        Text.from_markup(f"\n[bold white]DEVICE TELEMETRY DASHBOARD FOR {layout.device}[/]\n{escape(str(smart.get('model', 'N/A')))}  |  Serial: {escape(str(smart.get('serial', 'N/A')))}  |  Health: {health_str}\n"),
        Columns([
            Panel(_build_smart_table(smart), title="[bold white]S.M.A.R.T. Hardware Health[/]", border_style="dim", width=int(PANEL_WIDTH * 0.41)),
            Panel(_build_space_table(layout, scan_ratio, is_cleared=exec_discard), title="[bold white]Unallocated Space & Sampling[/]", border_style="dim", width=int(PANEL_WIDTH * 0.58))
        ]),
        sys_panel,
        Text.from_markup(f"\n[bold white]Physical Disk Sector Map Layout:[/]\n{draw_layout_bar(layout, visual_ratio)}\n[dim]{legend}[/]\n")
    )

    border = "green" if exec_discard or (hdd and scan_ratio is None) else ("cyan" if scan_ratio is None else "green" if scan_ratio == 0.0 else "yellow")
    console.print(Panel(Align.center(group), border_style=border, width=PANEL_WIDTH))
    console.print(_build_partition_table(layout, is_mock=is_mock))

    if scan_ratio is not None:
        console.print("[dim]Samples describe readable content only. Zero/FF bytes do not prove deallocation; nonzero bytes do not prove an active FTL mapping.[/]")
    if dry_run:
        commands = _build_discard_commands(layout, force=True, is_mock=is_mock)
        console.print(Panel(Text("\n".join(commands) or "No supported, aligned free extents to discard."),
                            title="Discard plan (dry run)", width=PANEL_WIDTH))
    if is_mock and exec_discard:
        console.print("[green]MOCK: planned extents completed; no device commands executed.[/]")


def render_glossary_panel() -> None:
    table = Table.grid(padding=(0, 2))
    table.add_column("Term", style="bold cyan", width=25)
    table.add_column("Explanation", style="white")
    for k, v in [
        ("Write Amplification (WAF):", "Ratio of physical data programmed to flash vs logical data written by the host. WAF 1.0 is optimal."),
        ("SMART Percentage Used:", "Firmware estimate of endurance consumed; this is not an overall health percentage."),
        ("LUKS Discard Passthrough:", "Encryption layers block block-deallocation by default. `allow_discards` permits TRIM to pass to the controller."),
        ("FS Mount Discard Flag:", "Mount options `discard` or `discard=async` request block deallocation; asynchronous discard batches requests."),
        ("Sector Samples:", "Readable bytes cannot establish controller allocation, write amplification, or remaining lifespan.")
    ]:
        table.add_row(k, v)
    console.print(Panel(table, title="[bold white]Diagnostic Guide & Parameter Explanations[/]", border_style="dim", width=PANEL_WIDTH))


def render_summary_table(summary_data: list[dict[str, Any]]) -> None:
    table = Table(title="Dusky Drive Health Summary", expand=True, width=PANEL_WIDTH)
    for column in ("Device", "Interface", "SMART / Endurance", "Unallocated %", "Sample Content", "Discard"):
        table.add_column(column)
    for data in summary_data:
        sample = data.get("dirty_ratio")
        table.add_row(Text(data["device"]), Text(data["interface"]), _health_text(data["smart"]),
                      "Unknown" if data["op"] is None else f"{data['op']:.2f}%", "Unknown / not scanned" if sample is None else f"{sample:.1%} nonzero/non-FF",
                      "Planned extents completed" if data.get("cleared") else "Not completed / not requested")
    console.print(table)


def interactive_menu() -> int:
    console.print(Align.center(Panel(
        "[bold cyan]INTERACTIVE MODE SELECTOR[/]\n"
        "[dim]Dusky Drive Health Diagnostics Selector[/]",
        border_style="cyan",
        expand=False
    )))

    table = Table(box=None, expand=False)
    table.add_column("Option", style="bold green", justify="right")
    table.add_column("Description", style="white")
    table.add_row("[1]", "Standard Diagnostics (SMART & Layouts)")
    table.add_row("[2]", "Sample Unallocated Sector Content")
    table.add_row("[3]", "Simulate Discards (Dry-run boundary logic)")
    table.add_row("[4]", "Sample and Discard Unallocated Space (Skip if samples are zero/FF)")
    table.add_row("[5]", "Exit")

    console.print(Align.center(table))
    console.print()

    while True:
        try:
            choice = console.input("[bold yellow]Enter choice (1-5): [/]").strip()
            if choice in {"1", "2", "3", "4", "5"}:
                return int(choice)
            console.print("[red]Invalid selection. Please enter 1, 2, 3, 4, or 5.[/]")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Interrupted. Exiting.[/]")
            return 5


def _elevate_privileges(choice: int, args: argparse.Namespace) -> None:
    if not sys.stdin.isatty():
        probe = _run(["sudo", "-n", "true"], timeout=3.0)
        if probe is None or probe.returncode != 0:
            console.print("[bold red][x] Error: Hardware diagnostics require root privileges, but session is non-interactive and sudo requires a password.[/]")
            console.print(f"Please run this command directly from your interactive terminal:\n  {shlex.join(['sudo', sys.executable, *sys.argv])}\n")
            sys.exit(1)

    console.print("[yellow][!] Hardware diagnostics require root privileges. Auto-elevating via sudo...[/]")
    target_args = ["sudo", "-E", sys.executable, sys.argv[0]]
    if choice > 0:
        target_args.extend(["--menu-executed", str(choice)])
    if args.device:
        target_args.extend(["--device", args.device])
    if args.scan:
        target_args.append("--scan")
    if args.dry_run_discard:
        target_args.append("--dry-run-discard")
    if args.execute_discard:
        target_args.append("--execute-discard")
    try:
        os.execvp("sudo", target_args)
    except OSError as e:
        console.print(f"[bold red][x] Privilege auto-elevation failed: {e}[/]")
        sys.exit(1)


# =============================================================================
# Main
# =============================================================================
def main() -> None:
    # Preserve the selected encoding, but do not crash on dashboard glyphs in ASCII logs.
    with suppress(AttributeError):
        sys.stdout.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(description="Dusky Drive Health Diagnostic Suite", epilog="Arch Linux Kernel 7.3 Multi-Interface Storage Analyzer")
    parser.add_argument("-v", "--version", action="version", version=f"Dusky Drive Health v{VERSION}")
    parser.add_argument("--mock", action="store_true", help="Execute in safe isolation demonstration mode with mock profiles.")
    parser.add_argument("--scan", action="store_true", help="Perform real read-only unallocated sector scan (requires root privileges).")
    parser.add_argument("--device", type=str, default=None, help="Path of physical drive to target (e.g. /dev/nvme0n1 or /dev/sda)")
    discard_modes = parser.add_mutually_exclusive_group()
    discard_modes.add_argument("--dry-run-discard", action="store_true", help="Dry run the recommended discard operations to verify bounds.")
    discard_modes.add_argument("--execute-discard", action="store_true", help="EXECUTE active discard operations on unallocated gaps.")
    parser.add_argument("--menu-executed", type=int, choices=range(1, 6), default=0, help=argparse.SUPPRESS)
    args = parser.parse_args()

    choice = args.menu_executed

    if choice == 5:
        return

    if len(sys.argv) == 1:
        choice = interactive_menu()
        if choice == 5:
            sys.exit(0)

    run_scan = args.scan or choice in (2, 4)
    dry_run = args.dry_run_discard or choice == 3
    exec_discard = args.execute_discard or choice == 4
    if dry_run and exec_discard:
        parser.error("Dry run and live discard cannot be combined")

    if not args.mock and os.geteuid() != 0:
        _elevate_privileges(choice, args)

    console.print(Align.center(Panel(
        "[bold cyan]DUSKY DRIVE HEALTH DIAGNOSTIC SUITE[/]\n"
        "[dim]Linux Kernel 7.3+ & Python 3.15+ Modern Storage Engine Diagnostics[/]",
        border_style="cyan",
        expand=False
    )))

    if args.mock:
        console.print("[bold green][*] Mode: Safe Isolation Mock Demonstration[/]")
        console.print("[dim]Simulating diagnostics for QLC NVMe, TLC NVMe, and SATA SSD drives...[/]\n")
        render_drive_diagnostics(MOCK_INTEL_LAYOUT, MOCK_INTEL_SMART, scan_ratio=0.0, dry_run=dry_run, exec_discard=False, is_mock=True)
        render_drive_diagnostics(MOCK_SAMSUNG_LAYOUT, MOCK_SAMSUNG_SMART, scan_ratio=0.0, dry_run=dry_run, exec_discard=False, is_mock=True)
        render_drive_diagnostics(MOCK_SATA_LAYOUT, MOCK_SATA_SMART, scan_ratio=0.35, dry_run=dry_run, exec_discard=exec_discard, is_mock=True)
        render_glossary_panel()
        mock_summary = [
            {"device": layout.device, "interface": smart["interface"], "smart": smart,
             "op": sum(end - start + 1 for start, end in layout.unallocated_gaps) / layout.total_sectors * 100,
             "dirty_ratio": ratio, "cleared": exec_discard and ratio > 0}
            for layout, smart, ratio in (
                (MOCK_INTEL_LAYOUT, MOCK_INTEL_SMART, 0.0),
                (MOCK_SAMSUNG_LAYOUT, MOCK_SAMSUNG_SMART, 0.0),
                (MOCK_SATA_LAYOUT, MOCK_SATA_SMART, 0.35),
            )
        ]
        render_summary_table(mock_summary)
        sys.exit(0)

    devices = []
    if args.device:
        try:
            mode = os.stat(args.device).st_mode
            if not stat.S_ISBLK(mode):
                console.print(f"[bold red][!] Specified path is not a valid block device node: {args.device}[/]")
                sys.exit(1)
            device = os.path.realpath(args.device)
            if not os.path.isdir(f"/sys/block/{os.path.basename(device)}"):
                parser.error("--device must name a whole disk, not a partition")
            devices = [device]
        except OSError:
            console.print(f"[bold red][!] Specified device path does not exist: {args.device}[/]")
            sys.exit(1)
    else:
        devices = detect_ssd_devices()

    if not devices:
        console.print(f"[bold red][!] No physical NVMe/SATA/SCSI drives detected on this host.[/]")
        sys.exit(1)

    summary_data = []
    failed = False
    for dev in devices:
        if not (layout := parse_partition_table(dev)):
            failed = True
            continue

        dev_is_rotational = is_rotational(dev)

        try:
            smart = query_live_smart_data(dev)
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as error:
            console.print(Text(f"SMART parsing failed for {dev}: {error}"))
            smart = None
        if not smart:
            failed = True
            console.print(f"[yellow]SMART telemetry unavailable for {dev}.[/]")
            flash_type = detect_flash_type(layout.model, is_hdd=dev_is_rotational)
            smart = {
                "device": dev,
                "model": layout.model,
                "serial": "N/A",
                "firmware": "N/A",
                "flash_type": flash_type,
                "interface": "Unknown (Rotational HDD)" if dev_is_rotational else "Unknown"
            }

        scan_ratio = None
        if not dev_is_rotational and run_scan and layout.unallocated_gaps:
            scan_ratio = scan_unallocated_regions(dev, layout.unallocated_gaps, layout.sector_size)

        cleared = False
        if run_scan and not dev_is_rotational and layout.unallocated_gaps and scan_ratio is None:
            failed = True
        if exec_discard and not dev_is_rotational:
            if run_scan and scan_ratio is None and layout.unallocated_gaps:
                console.print("[red]Scan failed; discard skipped.[/]")
            elif not layout.unallocated_gaps and layout.label != "none":
                console.print("[dim]No unallocated extents of at least 1 MiB; nothing to discard.[/]")
            elif scan_ratio == 0.0:
                console.print("[yellow]All samples are zero/FF; sample-based clearing skipped. This does not prove deallocation.[/]")
            else:
                cleared = execute_discards(layout)
                failed |= not cleared
        elif exec_discard:
            console.print("[yellow]Discard skipped: rotational or unknown device capability.[/]")
            failed = True

        render_drive_diagnostics(layout, smart, scan_ratio, dry_run=dry_run, exec_discard=cleared)

        summary_data.append({
            "device": dev,
            "interface": smart.get("interface", "NVMe"),
            "op": None if layout.label == "none" else sum(end - start + 1 for start, end in layout.unallocated_gaps) / layout.total_sectors * 100,
            "dirty_ratio": scan_ratio,
            "cleared": cleared,
            "smart": smart,
        })

    render_glossary_panel()
    if summary_data:
        render_summary_table(summary_data)
    if failed or not summary_data:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted. Exiting cleanly.[/]")
        sys.exit(130)
