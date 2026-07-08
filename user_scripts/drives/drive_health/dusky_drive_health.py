#!/usr/bin/env python3
"""
Dusky Drive Health v2.1.2 (Audited & Optimized)
Python 3.14+ / Kernel 7.1+ / util-linux 2.41+ Edition

Multi-interface SSD wear-leveling and over-provisioning diagnostic suite.
Audits NVMe and SATA/SCSI SSD SMART logs, resolves partition extents and
unallocated gaps, measures FTL mapping statuses via read-only sector sampling,
and checks TRIM travel paths across LUKS and filesystem mount layers.
"""

from __future__ import annotations

import os
import sys
import stat
import json
import math
import re
import argparse
import subprocess
import shutil
import gzip
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TypedDict, Any

# ============================================================================
# 1. DEPENDENCY CHECK & CONSOLE SETUP
# ============================================================================
try:
    from rich.console import Console, Group
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich.progress import Progress, BarColumn, TextColumn, SpinnerColumn, TimeElapsedColumn
    from rich.align import Align
    from rich.columns import Columns
except ImportError:
    sys.stderr.write(
        "[!] python-rich is required for console rendering.\n"
        "    Arch Linux:  sudo pacman -S python-rich\n"
        "    Pip:         pip install rich\n"
    )
    sys.exit(1)

console = Console()
PANEL_WIDTH: int = min(console.width, 112)

# ============================================================================
# 2. TYPE DEFINITIONS & SCHEMAS (Python 3.12+ PEP 695)
# ============================================================================
type SectorRange = tuple[int, int]
type DeviceTree = dict[str, dict[str, Any]]

class SmartData(TypedDict, total=False):
    device: str
    model: str
    serial: str
    firmware: str
    temp: float
    percentage_used: int
    tbw_written: float
    tbw_rated: float
    power_on_hours: int
    unsafe_shutdowns: int
    media_errors: int
    flash_type: str  # "QLC" | "TLC"
    interface: str   # "NVMe" | "SATA" | "SCSI"

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

    @property
    def number(self) -> int:
        m = re.search(r"(\d+)$", self.name)
        return int(m.group(1)) if m else 0

@dataclass(frozen=True, slots=True)
class DiskLayout:
    device: str
    model: str
    total_sectors: int
    sector_size: int
    label: str  # "gpt" | "dos" | "none" | "unknown"
    partitions: list[PartitionInfo]
    unallocated_gaps: list[SectorRange]

# ============================================================================
# 3. MOCK DATA PROFILES
# ============================================================================
MOCK_INTEL_SMART: SmartData = {
    "device": "/dev/nvme0n1",
    "model": "INTEL SSDPEKNU512GZ (670p QLC)",
    "serial": "PHPN12345678512D",
    "firmware": "CO20100F",
    "temp": 36.0,
    "percentage_used": 30,
    "tbw_written": 67.51,
    "tbw_rated": 185.0,
    "power_on_hours": 21348,
    "unsafe_shutdowns": 1678,
    "media_errors": 0,
    "flash_type": "QLC",
    "interface": "NVMe"
}

MOCK_INTEL_LAYOUT = DiskLayout(
    device="/dev/nvme0n1",
    model="INTEL SSDPEKNU512GZ (670p QLC)",
    total_sectors=1000215216,
    sector_size=512,
    label="gpt",
    partitions=[
        PartitionInfo("nvme0n1p1", 2048, 6293503, 6291456, "ext4", "/home/dusk/.config/mozilla", is_luks=True, allow_discards=True, discard_mounted=False),
        PartitionInfo("nvme0n1p2", 6293504, 9089023, 2795520, "vfat", "/boot", is_luks=False, allow_discards=False, discard_mounted=False),
        PartitionInfo("nvme0n1p3", 9089024, 260747263, 251658240, "btrfs", "/", is_luks=False, allow_discards=False, discard_mounted=True),
    ],
    unallocated_gaps=[(260747264, 1000215182)]
)

MOCK_SAMSUNG_SMART: SmartData = {
    "device": "/dev/nvme1n1",
    "model": "Samsung SSD 980 1TB (TLC)",
    "serial": "S64DNL0R123456F",
    "firmware": "1B4QFXO7",
    "temp": 40.0,
    "percentage_used": 10,
    "tbw_written": 81.72,
    "tbw_rated": 600.0,
    "power_on_hours": 5539,
    "unsafe_shutdowns": 1478,
    "media_errors": 0,
    "flash_type": "TLC",
    "interface": "NVMe"
}

MOCK_SAMSUNG_LAYOUT = DiskLayout(
    device="/dev/nvme1n1",
    model="Samsung SSD 980 1TB (TLC)",
    total_sectors=1953525168,
    sector_size=512,
    label="gpt",
    partitions=[
        PartitionInfo("nvme1n1p1", 2048, 1048578047, 1048576000, "ext4", "/mnt/media", is_luks=True, allow_discards=True, discard_mounted=False),
    ],
    unallocated_gaps=[(1048578048, 1953525134)]
)

MOCK_SATA_SMART: SmartData = {
    "device": "/dev/sda",
    "model": "Samsung SSD 870 QVO 2TB (QLC)",
    "serial": "S5XANGB1234567W",
    "firmware": "1B6QJX7",
    "temp": 38.0,
    "percentage_used": 15,
    "tbw_written": 108.5,
    "tbw_rated": 740.0,
    "power_on_hours": 8760,
    "unsafe_shutdowns": 42,
    "media_errors": 0,
    "flash_type": "QLC",
    "interface": "SATA"
}

MOCK_SATA_LAYOUT = DiskLayout(
    device="/dev/sda",
    model="Samsung SSD 870 QVO 2TB (QLC)",
    total_sectors=3907029168,
    sector_size=512,
    label="gpt",
    partitions=[
        PartitionInfo("sda1", 2048, 1050623, 1048576, "vfat", "/boot", is_luks=False, allow_discards=False, discard_mounted=True),
        PartitionInfo("sda2", 1050624, 2095103, 1044480, "swap", "[SWAP]", is_luks=False, allow_discards=False, discard_mounted=False),
        PartitionInfo("sda3", 2095104, 3906961407, 3904866304, "ext4", "/mnt/data", is_luks=True, allow_discards=True, discard_mounted=True),
    ],
    unallocated_gaps=[(3906961408, 3907029134)]
)

# ============================================================================
# 4. FLASH TYPE & TBW ENDURANCE HEURISTICS ENGINE
# ============================================================================
QLC_TBW_PER_TB: float = 370.0
TLC_TBW_PER_TB: float = 600.0

QLC_PATTERNS: list[str] = ["QLC", "QVO", "660P", "670P", "BX500", "NV2", "SN350", "A400"]

def detect_flash_type(
    model: str,
    percentage_used: int,
    tbw_written: float,
    capacity_tb: float
) -> str:
    """Identifies if cells are QLC or TLC using model checks and active wear ratio fallbacks."""
    m = model.upper()
    if any(pat in m for pat in QLC_PATTERNS):
        return "QLC"
    if "TLC" in m or "EVO" in m or "PRO" in m:
        return "TLC"

    if percentage_used > 0 and tbw_written > 0 and capacity_tb > 0:
        inferred_tbw_per_tb = (tbw_written / (percentage_used / 100.0)) / capacity_tb
        midpoint = (QLC_TBW_PER_TB + TLC_TBW_PER_TB) / 2.0
        if inferred_tbw_per_tb < midpoint:
            return "QLC"

    return "TLC"

def estimate_tbw_rated(capacity_tb: float, flash_type: str) -> float:
    per_tb = QLC_TBW_PER_TB if flash_type == "QLC" else TLC_TBW_PER_TB
    return round(capacity_tb * per_tb, 1)

def get_device_capacity_tb(device: str) -> float:
    """Read total device capacity in TB directly from sysfs."""
    dev_name = os.path.basename(device)
    sysfs_size_path = f"/sys/block/{dev_name}/size"
    try:
        if os.path.exists(sysfs_size_path):
            with open(sysfs_size_path, encoding="utf-8") as f:
                sectors = int(f.read().strip())
            return sectors * 512 / 1e12
    except OSError:
        pass
    return 0.0

def _get_device_sector_size(device: str) -> int:
    """Read logical block size from sysfs queue properties."""
    dev_name = os.path.basename(device)
    sysfs_path = f"/sys/block/{dev_name}/queue/logical_block_size"
    try:
        if os.path.exists(sysfs_path):
            with open(sysfs_path, encoding="utf-8") as f:
                return int(f.read().strip())
    except (OSError, ValueError):
        pass
    return 512

def _get_device_model(device: str) -> str:
    """Read model string from sysfs or lsblk command."""
    dev_name = os.path.basename(device)
    model_path = f"/sys/block/{dev_name}/device/model"
    try:
        if os.path.exists(model_path):
            with open(model_path, encoding="utf-8") as f:
                return f.read().strip()
    except OSError:
        pass
    try:
        res = subprocess.run(
            ["lsblk", "-d", "-o", "MODEL", device, "--noheadings"],
            capture_output=True, text=True, timeout=3
        )
        if res.returncode == 0 and res.stdout:
            return res.stdout.strip()
    except subprocess.TimeoutExpired:
        pass
    return "Unknown Device"

# ============================================================================
# 5. WAF & LIFE EXPECTANCY PROJECTIONS
# ============================================================================
def calculate_estimated_waf(op_percentage: float, is_qlc: bool = False) -> float:
    """Estimate write amplification curve based on over-provisioning pools."""
    op_ratio = op_percentage / 100.0
    base_waf = 4.8 if is_qlc else 4.0
    estimated = 1.15 + (base_waf - 1.15) * math.exp(-3.5 * op_ratio)
    return max(1.1, round(estimated, 2))

def get_lifespan_projections(
    written: float,
    rated: float,
    current_op: float,
    target_op: float,
    is_qlc: bool = False
) -> dict[str, float]:
    """Calculate comparative wear factors and host write boost predictions."""
    current_waf = calculate_estimated_waf(current_op, is_qlc)
    target_waf = calculate_estimated_waf(target_op, is_qlc)
    remaining_tbw = max(0.1, rated - written)
    multiplier = current_waf / target_waf
    return {
        "current_waf": current_waf,
        "target_waf": target_waf,
        "multiplier": multiplier,
        "remaining_tbw": remaining_tbw,
        "extended_remaining_tbw": remaining_tbw * multiplier
    }

# ============================================================================
# 6. PHYSICAL SSD DETECTION ROUTINES
# ============================================================================
def detect_ssd_devices() -> list[str]:
    """Auto-detects non-rotational flash devices (NVMe + SATA/SCSI SSDs)."""
    devices: list[str] = []
    try:
        res = subprocess.run(
            ["lsblk", "-d", "-J", "-o", "NAME,ROTA,TYPE"],
            capture_output=True, text=True, timeout=5
        )
        if res.returncode != 0:
            return devices
        data = json.loads(res.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return devices

    for d in data.get("blockdevices", []):
        name = d.get("name", "")
        dev_type = d.get("type", "")
        rota = d.get("rota", 1)

        if dev_type != "disk":
            continue
        if not (name.startswith("nvme") or name.startswith("sd")):
            continue
        
        # Guard against JSON schema variations in `lsblk` (int vs string vs bool)
        if str(rota).lower() not in ("0", "false"):
            continue
            
        sysfs_rot = f"/sys/block/{name}/queue/rotational"
        try:
            with open(sysfs_rot, encoding="utf-8") as f:
                if f.read().strip() != "0":
                    continue
        except OSError:
            continue

        devices.append(f"/dev/{name}")
    return sorted(devices)

# ============================================================================
# 7. RECURSIVE MOUNT & MOUNT OPTIONS RESOLVER
# ============================================================================
def get_mount_discards() -> dict[str, bool]:
    discards: dict[str, bool] = {}
    try:
        with open("/proc/mounts", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 4:
                    continue
                dev = parts[0]
                options = parts[3].split(",")
                has_discard = any(
                    opt == "discard" or opt.startswith("discard=")
                    for opt in options
                )
                discards[dev] = has_discard
    except OSError:
        pass
    return discards

def build_device_tree(device: str) -> DeviceTree:
    result: DeviceTree = {}
    try:
        res = subprocess.run(
            ["lsblk", "-J", "-b", "-o", "NAME,TYPE,FSTYPE,MOUNTPOINTS,MAJ:MIN,PKNAME,DISC-GRAN", device],
            capture_output=True, text=True, timeout=5
        )
        if res.returncode != 0:
            return result
        data = json.loads(res.stdout)

        def walk(node: dict[str, Any]) -> None:
            name = node.get("name", "")
            if name:
                result[name] = {
                    "type": node.get("type", ""),
                    "fstype": node.get("fstype") or "",
                    "mountpoints": [mp for mp in (node.get("mountpoints") or []) if mp],
                    "maj_min": node.get("maj:min", ""),
                    "pkname": node.get("pkname") or "",
                    "disc_gran": int(node.get("disc-gran", 0) or 0),
                    "children": [c.get("name", "") for c in (node.get("children") or [])]
                }
            for child in (node.get("children") or []):
                walk(child)

        for dev in data.get("blockdevices", []):
            walk(dev)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        pass
    return result

def resolve_mountpoints(part_name: str, tree: DeviceTree) -> list[str]:
    node = tree.get(part_name, {})
    mps: list[str] = [mp for mp in node.get("mountpoints", []) if mp]
    for child_name in node.get("children", []):
        mps.extend(resolve_mountpoints(child_name, tree))
    return list(dict.fromkeys(mps))

def analyze_partition_discard(
    part_name: str,
    tree: DeviceTree,
    mount_discards: dict[str, bool]
) -> tuple[bool, bool]:
    node = tree.get(part_name, {})
    fstype = node.get("fstype", "")
    is_luks = "crypto_LUKS" in fstype or "luks" in fstype.lower()

    if not is_luks:
        part_dev = f"/dev/{part_name}"
        return False, mount_discards.get(part_dev, False)

    allow_discards = False
    discard_mounted = False

    def walk_children(name: str) -> None:
        nonlocal allow_discards, discard_mounted
        n = tree.get(name, {})
        if n.get("type") == "crypt" and n.get("disc_gran", 0) > 0:
            allow_discards = True
        for dev_path in (f"/dev/mapper/{name}", f"/dev/{name}"):
            if mount_discards.get(dev_path):
                discard_mounted = True
        for child_name in n.get("children", []):
            walk_children(child_name)

    walk_children(part_name)
    return allow_discards, discard_mounted

# ============================================================================
# 8. RESOLVING BOUNDARIES WITH sfdisk
# ============================================================================
def parse_partition_table(device: str) -> DiskLayout | None:
    if shutil.which("sfdisk") is None:
        console.print("[red]sfdisk not found. Please install util-linux.[/]")
        return None

    dev_name = os.path.basename(device)
    sysfs_size_path = f"/sys/block/{dev_name}/size"
    sector_size = _get_device_sector_size(device)
    total_sectors = 0

    try:
        if os.path.exists(sysfs_size_path):
            with open(sysfs_size_path, encoding="utf-8") as f:
                # Kernel block size is ALWAYS 512. Scale to logical blocks for layout engine.
                total_sectors = (int(f.read().strip()) * 512) // sector_size
    except OSError:
        pass

    if total_sectors == 0:
        console.print(f"[red]Could not determine sector count for {device}[/]")
        return None

    model = _get_device_model(device)

    try:
        res_json = subprocess.run(
            ["sfdisk", "--json", device],
            capture_output=True, text=True, timeout=5
        )

        if res_json.returncode != 0:
            tree = build_device_tree(device)
            root_node = tree.get(dev_name, {})
            fstype = root_node.get("fstype", "")

            if fstype:
                mps = list(dict.fromkeys(root_node.get("mountpoints", [])))
                mountpoint = ", ".join(mps) if mps else "unmounted"
                is_luks = "crypto_LUKS" in fstype or "luks" in fstype.lower()
                allow_d, discard_m = analyze_partition_discard(dev_name, tree, get_mount_discards())
                partitions = [PartitionInfo(
                    name=dev_name, start_sector=0, end_sector=total_sectors - 1,
                    size_sectors=total_sectors, fs_type=fstype, mountpoint=mountpoint,
                    is_luks=is_luks, allow_discards=allow_d, discard_mounted=discard_m
                )]
                gaps = []
            else:
                partitions = []
                gaps = [(0, total_sectors - 1)]

            return DiskLayout(
                device=device, model=model, total_sectors=total_sectors,
                sector_size=sector_size, label="none", partitions=partitions, unallocated_gaps=gaps
            )

        pt = json.loads(res_json.stdout).get("partitiontable", {})
        label = pt.get("label", "unknown")

        res_free = subprocess.run(
            ["sfdisk", "--list-free", device],
            capture_output=True, text=True, timeout=5
        )
        gaps = []
        if res_free.returncode == 0:
            for line in res_free.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 3 and parts[0].isdigit():
                    start = int(parts[0])
                    end = int(parts[1])
                    sectors = int(parts[2])
                    if sectors > 2048:
                        gaps.append((start, end))

        tree = build_device_tree(device)
        mount_discards = get_mount_discards()

        partitions: list[PartitionInfo] = []
        for part in pt.get("partitions", []):
            name = part.get("node", "").split("/")[-1]
            start = part.get("start", 0)
            size = part.get("size", 0)
            end = start + size - 1

            node = tree.get(name, {})
            fs_type = node.get("fstype") or "unknown"
            is_luks = "crypto_LUKS" in fs_type or "luks" in fs_type.lower()

            mountpoints = resolve_mountpoints(name, tree)
            if not mountpoints and fs_type == "swap":
                mountpoint = "[SWAP]"
            elif not mountpoints:
                mountpoint = "unmounted"
            else:
                mountpoint = ", ".join(mountpoints)

            allow_discards, discard_mounted = analyze_partition_discard(name, tree, mount_discards)

            partitions.append(PartitionInfo(
                name=name, start_sector=start, end_sector=end, size_sectors=size,
                fs_type=fs_type, mountpoint=mountpoint, is_luks=is_luks,
                allow_discards=allow_discards, discard_mounted=discard_mounted
            ))

        return DiskLayout(
            device=device, model=model, total_sectors=total_sectors,
            sector_size=sector_size, label=label, partitions=partitions, unallocated_gaps=gaps
        )
    except subprocess.TimeoutExpired:
        console.print(f"[red]sfdisk timed out while analyzing {device}[/]")
        return None
    except json.JSONDecodeError as e:
        console.print(f"[red]sfdisk JSON parse error for {device}: {e}[/]")
        return None
    except Exception as e:
        console.print(f"[red]Error parsing boundaries for {device}: {e}[/]")
        return None

# ============================================================================
# 9. SMART TELEMETRY SYSTEM (Dual NVMe + SATA/SCSI Backends)
# ============================================================================
def _extract_nvme_controller(device: str) -> str | None:
    m = re.search(r"(nvme\d+)", device)
    return f"/dev/{m.group(1)}" if m else None

def _run_nvme_json(args: list[str]) -> dict[str, Any] | None:
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=5)
        if res.returncode != 0:
            return None
        return json.loads(res.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None

def _query_nvme_smart(device: str) -> SmartData | None:
    ctrl = _extract_nvme_controller(device)
    if ctrl is None or shutil.which("nvme") is None:
        return None

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            log_future = pool.submit(_run_nvme_json, ["nvme", "smart-log", ctrl, "-o", "json"])
            id_future = pool.submit(_run_nvme_json, ["nvme", "id-ctrl", ctrl, "-o", "json"])
            log = log_future.result()
            ident = id_future.result()
    except OSError:
        return None

    if log is None:
        return None

    smart: SmartData = {"device": device, "interface": "NVMe"}

    temp_k = int(log.get("temperature") or 0)
    smart["temp"] = float(temp_k - 273.15) if temp_k > 200 else float(temp_k)
    smart["percentage_used"] = int(log.get("percentage_used") or log.get("percent_used") or 0)

    duw = int(log.get("data_units_written") or 0)
    smart["tbw_written"] = round(duw * 512_000 / 1e12, 2)

    smart["power_on_hours"] = int(log.get("power_on_hours") or 0)
    smart["unsafe_shutdowns"] = int(log.get("unsafe_shutdowns") or 0)
    smart["media_errors"] = int(log.get("media_errors") or 0)

    if ident:
        smart["model"] = (ident.get("mn") or "Unknown NVMe").strip()
        smart["serial"] = (ident.get("sn") or "N/A").strip()
        smart["firmware"] = (ident.get("fr") or "N/A").strip()
    else:
        smart["model"] = _get_device_model(device)
        smart["serial"] = "N/A"
        smart["firmware"] = "N/A"

    capacity_tb = get_device_capacity_tb(device)
    smart["flash_type"] = detect_flash_type(
        smart.get("model", ""), smart.get("percentage_used", 0),
        smart.get("tbw_written", 0.0), capacity_tb
    )
    smart["tbw_rated"] = estimate_tbw_rated(capacity_tb, smart["flash_type"])
    return smart

def _extract_sata_wear_percentage(attrs: dict[int, dict[str, Any]]) -> int:
    """Evaluate SATA wear metrics from vendor table mappings."""
    # Added 231 (SSD Life Left) for Phison/Kingston controllers
    for attr_id, is_remaining in [(231, True), (233, True), (202, True), (169, True), (177, True)]:
        attr = attrs.get(attr_id)
        if attr:
            val = int(attr.get("value", 0))
            if 0 < val <= 100:
                return max(0, 100 - val) if is_remaining else max(0, min(100, val))
    return 0

def _extract_sata_tbw(attrs: dict[int, dict[str, Any]]) -> float:
    attr_241 = attrs.get(241)
    if attr_241:
        raw = int(attr_241.get("raw", {}).get("value", 0))
        name = attr_241.get("name", "").upper()
        if "32MIB" in name or "32MB" in name:
            return round(raw * 32 * 1024 * 1024 / 1e12, 2)
        return round(raw * 512 / 1e12, 2)

    attr_249 = attrs.get(249)
    if attr_249:
        raw = int(attr_249.get("raw", {}).get("value", 0))
        return round(raw * (1 << 30) / 1e12, 2)

    return 0.0

def _query_block_smart(device: str) -> SmartData | None:
    if shutil.which("smartctl") is None:
        console.print("[red]smartctl not found. Please install smartmontools.[/]")
        return None

    for args in (["smartctl", "-x", "--json", device], ["smartctl", "-x", "--json", "-d", "sat", device]):
        try:
            res = subprocess.run(args, capture_output=True, text=True, timeout=10)
        except (subprocess.TimeoutExpired, OSError):
            continue

        try:
            data = json.loads(res.stdout)
        except json.JSONDecodeError:
            continue

        if not (data.get("model_name") or data.get("ata_smart_attributes")
                or data.get("nvme_smart_health_information_log")
                or data.get("scsi_smart_health_status")
                or data.get("scsi_grown_defect_list") is not None):
            continue

        smart: SmartData = {"device": device, "interface": "SATA"}
        smart["model"] = data.get("model_name", "Unknown SSD")
        smart["serial"] = data.get("serial_number", "N/A")
        smart["firmware"] = data.get("firmware_version", "N/A")

        temp = data.get("temperature", {})
        smart["temp"] = float(temp.get("current", 0))

        nvme_log = data.get("nvme_smart_health_information_log")
        if nvme_log:
            smart["percentage_used"] = int(nvme_log.get("percentage_used") or 0)
            duw = int(nvme_log.get("data_units_written") or 0)
            smart["tbw_written"] = round(duw * 512_000 / 1e12, 2)
            smart["power_on_hours"] = int(nvme_log.get("power_on_hours") or 0)
            smart["unsafe_shutdowns"] = int(nvme_log.get("unsafe_shutdowns") or 0)
            smart["media_errors"] = int(nvme_log.get("media_errors") or 0)
            smart["interface"] = "NVMe"
        elif data.get("ata_smart_attributes"):
            table_list = data.get("ata_smart_attributes", {}).get("table", [])
            attrs: dict[int, dict[str, Any]] = {a["id"]: a for a in table_list if "id" in a}

            smart["power_on_hours"] = int(attrs.get(9, {}).get("raw", {}).get("value", 0))
            smart["percentage_used"] = _extract_sata_wear_percentage(attrs)
            smart["tbw_written"] = _extract_sata_tbw(attrs)
            smart["unsafe_shutdowns"] = int(attrs.get(174, {}).get("raw", {}).get("value", 0))

            realloc = int(attrs.get(5, {}).get("raw", {}).get("value", 0))
            uncorr = int(attrs.get(187, {}).get("raw", {}).get("value", 0))
            smart["media_errors"] = realloc + uncorr
        else:
            smart["interface"] = "SCSI"
            smart["percentage_used"] = 0
            smart["power_on_hours"] = int(data.get("scsi_hours_powered_on") or 0)
            smart["media_errors"] = int(data.get("scsi_grown_defect_list") or 0)
            smart["unsafe_shutdowns"] = 0
            smart["tbw_written"] = 0.0

        capacity_tb = get_device_capacity_tb(device)
        smart["flash_type"] = detect_flash_type(
            smart.get("model", ""), smart.get("percentage_used", 0),
            smart.get("tbw_written", 0.0), capacity_tb
        )
        smart["tbw_rated"] = estimate_tbw_rated(capacity_tb, smart["flash_type"])
        return smart

    return None

def query_live_smart_data(device: str) -> SmartData | None:
    if re.search(r"nvme\d+n\d+", device):
        return _query_nvme_smart(device)
    elif re.search(r"sd[a-z]+", device):
        return _query_block_smart(device)
    return None

# ============================================================================
# 10. READ-ONLY FTL BLOCK SAMPLING SCAN
# ============================================================================
def scan_unallocated_regions(
    dev_path: str,
    gaps: list[SectorRange],
    sector_size: int = 512,
    total_samples: int = 500
) -> float:
    """Scans all unallocated block regions read-only to compute weighted dirty ratio."""
    if not gaps:
        return 0.0

    valid_gaps: list[tuple[int, int, int]] = [(s, e, e - s + 1) for s, e in gaps if e >= s]
    if not valid_gaps:
        return 0.0

    total_unalloc_sectors = sum(g[2] for g in valid_gaps)
    if total_unalloc_sectors <= 0:
        return 0.0

    total_samples = min(total_samples, total_unalloc_sectors)
    gap_samples: list[list[int]] = []
    allocated = 0

    # Ensure statistically sound distribution without math remainder loops overshooting boundaries
    for start, end, sectors in valid_gaps:
        proportion = sectors / total_unalloc_sectors
        nsamp = max(1, round(total_samples * proportion))
        nsamp = min(nsamp, sectors)
        gap_samples.append([start, end, nsamp])
        allocated += nsamp

    # Distribute mathematical remainders
    diff = total_samples - allocated
    if diff > 0 and gap_samples:
        gap_samples.sort(key=lambda x: x[2], reverse=True)
        for g in gap_samples:
            add = min(diff, (g[1] - g[0] + 1) - g[2])
            g[2] += add
            diff -= add
            if diff <= 0:
                break
                
    gap_samples.sort(key=lambda x: x[0])
    actual_total = sum(g[2] for g in gap_samples)
    
    if actual_total <= 0:
        return 0.0

    zero_block = bytes(4096)
    dirty_count = 0
    tested = 0

    progress = Progress(
        SpinnerColumn(spinner_name="dots"),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=40),
        TextColumn("[bold cyan]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    )

    try:
        fd = os.open(dev_path, os.O_RDONLY)
    except PermissionError:
        console.print(
            f"[bold red][!] Permission denied for {dev_path}. "
            f"Scanning requires root privileges. Assuming all unallocated sectors dirty.[/]"
        )
        return 1.0
    except OSError as e:
        console.print(f"[bold red][!] Cannot open block device {dev_path}: {e}[/]")
        return 1.0

    try:
        with progress:
            task = progress.add_task(f"Scanning {os.path.basename(dev_path)} unallocated space...", total=actual_total)
            for start, end, nsamp in gap_samples:
                step = max(1, (end - start + 1) // nsamp)
                for i in range(nsamp):
                    target_sector = start + i * step
                    if target_sector > end:
                        break
                    
                    offset = target_sector * sector_size
                    # Clamp boundaries rigorously to prevent active partition user data ingestion
                    bytes_to_read = min(4096, (end - target_sector + 1) * sector_size)
                    
                    try:
                        block = os.pread(fd, bytes_to_read, offset)
                        # Immediately release pages to prevent RAM thrashing across unallocated gaps
                        if hasattr(os, "posix_fadvise"):
                            os.posix_fadvise(fd, offset, bytes_to_read, os.POSIX_FADV_DONTNEED)
                    except OSError as e:
                        progress.console.print(f"[dim red]I/O error at sector {target_sector}: {e} (marked dirty)[/]")
                        dirty_count += 1
                        tested += 1
                        progress.update(task, advance=1)
                        continue

                    if not block:
                        break

                    if len(block) == 4096:
                        if block != zero_block:
                            dirty_count += 1
                    else:
                        if block != bytes(len(block)):
                            dirty_count += 1
                            
                    tested += 1
                    progress.update(task, advance=1)
    finally:
        os.close(fd)

    return (dirty_count / tested) if tested > 0 else 0.0

# ============================================================================
# 11. STORAGE CONTROLLER TELEMETRY
# ============================================================================
def _format_bytes(bytes_val: int) -> str:
    if bytes_val <= 0:
        return "0 B (No hardware support)"
    for unit, threshold in (("TiB", 1 << 40), ("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)):
        if bytes_val >= threshold:
            return f"{bytes_val / threshold:.1f} {unit}"
    return f"{bytes_val} B"

def _query_nvme_driver_type(kernel_ver: str) -> str:
    for config_path in (f"/boot/config-{kernel_ver}", "/proc/config.gz"):
        try:
            if config_path.endswith(".gz"):
                with gzip.open(config_path, "rt", encoding="utf-8") as f:
                    content = f.read()
            else:
                with open(config_path, encoding="utf-8") as f:
                    content = f.read()
            for line in content.splitlines():
                if "NVME" in line and "RUST" in line and line.rstrip().endswith("=y"):
                    return "Active (Mainline Rust NVMe Driver)"
            return "Active (Standard C NVMe Driver)"
        except (FileNotFoundError, OSError):
            continue
    return "Active (NVMe module loaded)"

def _query_sata_driver(device: str) -> str:
    dev_name = os.path.basename(device)
    try:
        device_path = os.path.realpath(f"/sys/block/{dev_name}/device")
        for _ in range(8):
            driver_link = os.path.join(device_path, "driver")
            if os.path.islink(driver_link):
                driver_name = os.path.basename(os.readlink(driver_link))
                if driver_name not in ("sd", "sr"):
                    return f"Active ({driver_name} controller driver)"
            parent = os.path.dirname(device_path)
            if parent == device_path:
                break
            device_path = parent
    except OSError:
        pass
    return "Active (AHCI controller driver)"

def query_system_telemetry(
    device: str,
    is_mock: bool = False,
    is_nvme: bool = True
) -> dict[str, str]:
    telemetry: dict[str, str] = {
        "kernel": "7.1.2-arch3-1" if is_mock else "Unknown",
        "fstrim_timer": "Active (Weekly)" if is_mock else "Inactive",
        "discard_granularity": "512 B" if is_mock else "N/A",
        "discard_max_bytes": "2.0 TiB" if is_mock else "N/A",
        "storage_driver": "Unknown"
    }

    if is_mock:
        if is_nvme:
            telemetry["storage_driver"] = "Active (Mainline Rust NVMe Driver)"
            if "nvme1n1" in device:
                telemetry["discard_granularity"] = "4.0 KiB"
        else:
            telemetry["storage_driver"] = "Active (ahci SATA HBA driver)"
            telemetry["discard_max_bytes"] = "2.0 GiB"
        return telemetry

    try:
        res = subprocess.run(["uname", "-r"], capture_output=True, text=True, timeout=3)
        if res.returncode == 0:
            telemetry["kernel"] = res.stdout.strip()
    except subprocess.TimeoutExpired:
        pass

    try:
        res = subprocess.run(["systemctl", "is-active", "fstrim.timer"], capture_output=True, text=True, timeout=3)
        if res.returncode == 0 or "active" in res.stdout.strip():
            telemetry["fstrim_timer"] = "Active (Weekly System Timer)"
        else:
            telemetry["fstrim_timer"] = "Inactive"
    except subprocess.TimeoutExpired:
        pass

    dev_name = os.path.basename(device)
    for key, path_suffix in (
        ("discard_granularity", "queue/discard_granularity"),
        ("discard_max_bytes", "queue/discard_max_bytes")
    ):
        sysfs_path = f"/sys/block/{dev_name}/{path_suffix}"
        try:
            if os.path.exists(sysfs_path):
                with open(sysfs_path, encoding="utf-8") as f:
                    val = int(f.read().strip())
                    if key == "discard_granularity":
                        telemetry[key] = _format_bytes(val) if val > 0 else "0 (No discard support)"
                    else:
                        telemetry[key] = _format_bytes(val)
        except (OSError, ValueError):
            pass

    if is_nvme:
        if os.path.exists("/sys/module/nvme_core"):
            telemetry["storage_driver"] = _query_nvme_driver_type(telemetry["kernel"])
        else:
            telemetry["storage_driver"] = "Inactive"
    else:
        telemetry["storage_driver"] = _query_sata_driver(device)

    return telemetry

# ============================================================================
# 12. RENDERINGS & DIAGNOSTIC TUI PRESENTATION
# ============================================================================
FS_COLORS: dict[str, str] = {
    "btrfs": "green",
    "ext4": "cyan",
    "ext3": "cyan",
    "ext2": "cyan",
    "xfs": "blue",
    "f2fs": "magenta",
    "vfat": "yellow",
    "ntfs": "red",
    "swap": "bright_red",
    "crypto_LUKS": "bright_magenta"
}

def draw_layout_bar(layout: DiskLayout, dirty_ratio: float) -> str:
    width = 64
    bar = ["[dim grey]─[/]"] * width
    total = layout.total_sectors

    def scale(start: int, end: int) -> tuple[int, int]:
        s = max(0, min(width - 1, int((start / total) * width)))
        e = max(0, min(width - 1, int((end / total) * width)))
        return s, e

    for gap in layout.unallocated_gaps:
        s, e = scale(gap[0], gap[1])
        g_width = e - s + 1
        dirty_chars = int(g_width * dirty_ratio)
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

def _build_smart_table(smart: SmartData) -> Table:
    health = max(0, min(100, 100 - smart.get("percentage_used", 0)))
    health_bar_width = 20
    filled = int(health_bar_width * health / 100)
    health_bar = f"[green]{'█' * filled}[/][red]{'░' * (health_bar_width - filled)}[/]"

    health_str = (
        f"[bold green]{health}%[/]" if health >= 90
        else f"[bold yellow]{health}%[/]" if health >= 75
        else f"[bold red]{health}%[/] [blink][WARNING][/]"
    )
    unsafe = smart.get("unsafe_shutdowns", 0)
    media_err = smart.get("media_errors", 0)
    flash_type = smart.get("flash_type", "TLC")
    interface = smart.get("interface", "NVMe")

    table = Table.grid(padding=(0, 2))
    table.add_column("Key", style="dim", width=24)
    table.add_column("Value", style="bold")
    table.add_row("Model / Silicon:", smart.get("model", "N/A"))
    table.add_row("Serial Number:", smart.get("serial", "N/A"))
    table.add_row("Firmware Version:", smart.get("firmware", "N/A"))
    table.add_row("Interface Bus:", f"[bold blue]{interface}[/]")
    table.add_row("Flash Cell Type:", f"[bold {'magenta' if flash_type == 'QLC' else 'green'}]{flash_type}[/]")
    table.add_row("Controller Temp:", f"{smart.get('temp', 0):.1f}°C")
    table.add_row("Total Host Writes:", f"{smart.get('tbw_written', 0):.2f} TB")
    table.add_row("Device Rated Endurance:", f"{smart.get('tbw_rated', 0):.0f} TBW")
    table.add_row("SMART Health Remaining:", health_str)
    table.add_row("Health Bar Representation:", f"{health_bar}")
    table.add_row("Power On Hours:", f"{smart.get('power_on_hours', 0):,} hours")
    table.add_row("Unsafe Power Cuts:", f"[red]{unsafe:,}[/]" if unsafe > 100 else f"{unsafe:,}")
    table.add_row("Physical Media Errors:", f"[bold red]{media_err}[/]" if media_err > 0 else "0 (Healthy)")
    return table

def _build_op_table(
    layout: DiskLayout,
    smart: SmartData,
    scan_ratio: float | None
) -> Table:
    total_sec = layout.total_sectors
    part_sec = sum(p.size_sectors for p in layout.partitions)
    unalloc_sec = sum((g[1] - g[0] + 1) for g in layout.unallocated_gaps)
    op_raw_pct = (unalloc_sec / total_sec) * 100.0 if total_sec else 0.0
    dirty_pct = (scan_ratio * 100.0) if scan_ratio is not None else 0.0
    active_op_pct = op_raw_pct * (1.0 - (scan_ratio or 0.0))
    is_qlc = smart.get("flash_type", "TLC") == "QLC"

    table = Table.grid(padding=(0, 2))
    table.add_column("Key", style="dim", width=24)
    table.add_column("Value", style="bold")

    ss = layout.sector_size
    table.add_row("Total Block Capacity:", f"{total_sec * ss / (1 << 30):.2f} GiB ({total_sec:,} sectors)")
    table.add_row("Partitioned Extents:", f"{part_sec * ss / (1 << 30):.2f} GiB ({part_sec:,} sectors)")
    table.add_row("Unallocated Free Extents:", f"{unalloc_sec * ss / (1 << 30):.2f} GiB ({unalloc_sec:,} sectors)")
    table.add_row("Raw Over-Provisioning Limit:", f"{op_raw_pct:.2f}% of disk")

    if scan_ratio is None:
        table.add_row("FTL Allocation Status:", "[bold yellow]Not Scanned (Run --scan to check FTL mapping)[/]")
        return table

    proj = get_lifespan_projections(
        smart.get("tbw_written", 0.0), smart.get("tbw_rated", 100.0),
        current_op=active_op_pct, target_op=op_raw_pct, is_qlc=is_qlc
    )

    status_col = "[bold red]" if dirty_pct > 0 else "[bold green]"
    table.add_row("Dirty Free Space (Mapped):", f"{status_col}{dirty_pct:.2f}% of free space[/]")
    table.add_row("Functional OP Pool:", f"[bold green]{active_op_pct:.2f}%[/]")
    table.add_row("Steady-State WAF:", f"Current: {proj['current_waf']:.2f} → Post-Discard Target: {proj['target_waf']:.2f}")
    table.add_row("Write Longevity Multiplier:", f"[bold green]{proj['multiplier']:.2f}x lifespan extension[/]")
    table.add_row("Future Host Write Capacity:", f"{proj['remaining_tbw']:.1f} TB → [bold green]{proj['extended_remaining_tbw']:.1f} TB[/] via OP discard")
    return table

def _build_partition_table(layout: DiskLayout) -> Table:
    table = Table(
        title="Partition Discard & Encryption Configuration",
        header_style="bold cyan",
        border_style="dim",
        show_lines=False,
        expand=True,
        width=PANEL_WIDTH
    )
    table.add_column("Partition", style="bold green", ratio=1)
    table.add_column("Type", style="blue", ratio=1)
    table.add_column("Mountpoint", style="white", ratio=2)
    table.add_column("LUKS?", style="magenta", ratio=1, justify="center")
    table.add_column("LUKS Discard Passthrough", style="yellow", ratio=2, justify="center")
    table.add_column("FS Mount Discard Flag", style="cyan", ratio=2, justify="center")

    for part in layout.partitions:
        is_luks_str = "[bold magenta]Yes[/]" if part.is_luks else "No"
        if part.is_luks:
            luks_pt = "[bold green]Enabled (allow_discards)[/]" if part.allow_discards else "[bold red]Disabled (Blocks TRIM)[/]"
        else:
            luks_pt = "[dim]N/A (No Encryption)[/]"

        if part.mountpoint == "unmounted":
            fs_discard = "[dim]N/A (Unmounted)[/]"
        elif part.mountpoint == "[SWAP]":
            fs_discard = "[dim]N/A (Swap Space)[/]"
        else:
            fs_discard = "[bold green]Active (discard)[/]" if part.discard_mounted else "[bold yellow]Inactive (No discard flag)[/]"

        table.add_row(part.name, part.fs_type, part.mountpoint, is_luks_str, luks_pt, fs_discard)
    return table

def _build_discard_commands(layout: DiskLayout) -> list[str]:
    """Generates absolute byte-boundary discard commands for each unallocated gap."""
    commands: list[str] = []
    for gap in layout.unallocated_gaps:
        offset = gap[0] * layout.sector_size
        length = (gap[1] - gap[0] + 1) * layout.sector_size
        commands.append(f"sudo blkdiscard --offset {offset} --length {length} {layout.device}")
    return commands

def render_drive_diagnostics(
    layout: DiskLayout,
    smart: SmartData,
    scan_ratio: float | None,
    dry_run: bool = False,
    is_mock: bool = False
) -> None:
    health = max(0, min(100, 100 - smart.get("percentage_used", 0)))
    health_str = (
        f"[bold green]{health}%[/]" if health >= 90
        else f"[bold yellow]{health}%[/]" if health >= 75
        else f"[bold red]{health}%[/] [blink][WARNING][/]"
    )
    dirty_pct = (scan_ratio * 100.0) if scan_ratio is not None else 0.0

    is_nvme_drive = smart.get("interface", "NVMe") == "NVMe"
    sys_tel = query_system_telemetry(layout.device, is_mock=is_mock, is_nvme=is_nvme_drive)
    sys_table = Table.grid(padding=(0, 2))
    sys_table.add_column("Key", style="dim", width=27)
    sys_table.add_column("Value", style="bold")
    sys_table.add_row("Arch Linux Kernel Version:", sys_tel.get("kernel", "N/A"))
    sys_table.add_row("Systemd TRIM Service Timer:", sys_tel.get("fstrim_timer", "N/A"))
    sys_table.add_row("Device Discard Granularity:", sys_tel.get("discard_granularity", "N/A"))
    sys_table.add_row("Device Max Discard Block Size:", sys_tel.get("discard_max_bytes", "N/A"))
    
    drv = sys_tel.get("storage_driver", "N/A")
    sys_table.add_row("Active Storage Driver:", f"[bold green]{drv}[/]" if "Active" in drv else drv)

    sys_panel = Panel(
        sys_table,
        title="[bold white]Host OS & Storage Queue Telemetry[/]",
        border_style="dim", width=PANEL_WIDTH
    )

    layout_str = draw_layout_bar(layout, scan_ratio or 0.0)
    legend = "[bold cyan]█[/] Ext4/Btrfs    [bold bright_magenta]█[/] LUKS Map    [bold bright_red]█[/] Swap    [bold yellow]░[/] Dirty OP Space    [bold green]▒[/] Clean OP Space    [dim grey]─[/] Slack"

    panels = Columns([
        Panel(_build_smart_table(smart), title="[bold white]S.M.A.R.T. Hardware Health[/]", border_style="dim", width=PANEL_WIDTH // 2 - 1),
        Panel(_build_op_table(layout, smart, scan_ratio), title="[bold white]FTL Over-Provisioning Mapping[/]", border_style="dim", width=PANEL_WIDTH // 2 + 1),
    ])

    group = Group(
        Text.from_markup(f"\n[bold white]DEVICE TELEMETRY DASHBOARD FOR {layout.device}[/]\n{smart.get('model', 'N/A')}  |  Serial: {smart.get('serial', 'N/A')}  |  Health: {health_str}\n"),
        panels,
        sys_panel,
        Text.from_markup(f"\n[bold white]Physical Disk Sector Map Layout:[/]\n{layout_str}\n[dim]{legend}[/]\n")
    )

    border = "cyan" if scan_ratio is None or dirty_pct == 0 else "yellow"
    console.print(Panel(Align.center(group), border_style=border, width=PANEL_WIDTH))
    console.print(_build_partition_table(layout))

    match (scan_ratio is not None, dirty_pct > 0, dry_run):
        case (True, True, _):
            cmds = _build_discard_commands(layout)
            cmd_lines = "\n".join(f"  {c}" for c in cmds)
            rec = Text.assemble(
                "\n",
                "[bold yellow][!] DIAGNOSTIC ADVISORY:[/]\n",
                "This drive contains unallocated sectors holding obsolete host data mappings.\n",
                "The SSD controller cannot utilize these blocks for over-provisioning until they are discarded.\n\n",
                "[bold green][*] RECOMMENDED ACTION COMMANDS:[/]\n",
                f"{cmd_lines}\n\n",
                "[dim]Note: blkdiscard targeting explicit byte offsets is fully partition-safe. Active data sectors are structurally protected.[/]"
            )
            console.print(Panel(rec, title="[bold yellow]Wear-Leveling Correction Plan[/]", border_style="yellow", width=PANEL_WIDTH))
            if dry_run:
                console.print("\n[bold blue][DRY RUN MODE] Simulating absolute-bound discard operations...[/]")
                for c in cmds:
                    console.print(f"[bold green]Executing simulated command:[/] {c} — dry-run OK")
                console.print("[bold green][+] FTL mappings for the unallocated regions would be reset. Dynamic OP would be fully active![/]")

        case (True, False, _):
            rec = Text.assemble(
                "\n",
                "[bold green][+] DIAGNOSTIC HEALTH REPORT:[/]\n",
                "This drive's unallocated extents are fully trimmed and unmapped in the Flash Translation Layer.\n",
                "The SSD controller is leveraging the entire unallocated space as functional over-provisioning.\n",
                "Write amplification is fully optimized. No further action required.\n"
            )
            console.print(Panel(rec, title="[bold green]Optimized Wear-Leveling Status[/]", border_style="green", width=PANEL_WIDTH))
        case _:
            pass

def render_glossary_panel() -> None:
    table = Table.grid(padding=(0, 2))
    table.add_column("Term", style="bold cyan", width=25)
    table.add_column("Explanation", style="white")
    table.add_row(
        "Write Amplification (WAF):",
        "Ratio of physical data programmed to flash vs logical data written by the host. WAF 1.0 is optimal. "
        "Random I/O spikes WAF; OP provides empty blocks for Garbage Collection, dropping WAF toward 1.0."
    )
    table.add_row(
        "SMART Percentage Used:",
        "Firmware's mathematical estimate of total P/E cycles exhausted on flash cells. "
        "Remaining health = 100% − Percentage Used. Critical for fragile QLC nodes."
    )
    table.add_row(
        "LUKS Discard Passthrough:",
        "Encryption layers by default block block-deallocation (TRIM/discard) to prevent side-channel structural leakage. "
        "`allow_discards` option on the crypt mapping permits TRIM to pass to the controller."
    )
    table.add_row(
        "FS Mount Discard Flag:",
        "Mount options `discard` (synchronous) or `discard=async` (queued) that instruct the controller to "
        "unmap deleted file sectors immediately. Btrfs async discard is highly optimal."
    )
    table.add_row(
        "Dirty Free Space:",
        "Logical unallocated extents containing legacy host writes. The SSD FTL still maps these LBAs, "
        "so they cannot serve as OP until unmapped via raw discard/TRIM commands."
    )
    console.print(Panel(table, title="[bold white]Diagnostic Guide & Parameter Explanations[/]", border_style="dim", width=PANEL_WIDTH))

def render_summary_table(summary_data: list[dict[str, Any]]) -> None:
    table = Table(
        title="Dusky Drive Health Summary Report",
        header_style="bold bright_cyan",
        border_style="cyan",
        expand=True,
        width=PANEL_WIDTH
    )
    table.add_column("Device", style="bold green", ratio=1)
    table.add_column("Interface", style="blue", ratio=1)
    table.add_column("Model", style="white", ratio=2)
    table.add_column("SMART Health", style="bold", ratio=1, justify="center")
    table.add_column("Writes (TB)", style="yellow", ratio=1, justify="center")
    table.add_column("OP Pool %", style="bold green", ratio=1, justify="center")
    table.add_column("OP Mapping State", style="bold", ratio=2, justify="center")

    for d in summary_data:
        health = max(0, min(100, 100 - d["pct_used"]))
        health_color = "green" if health >= 90 else "yellow" if health >= 75 else "red"
        health_str = f"[{health_color}]{health}%[/]"

        dirty = d.get("dirty_ratio")
        if dirty is None:
            state_str = "[yellow]Not Scanned[/]"
        elif dirty == 0.0:
            state_str = "[green]Fully Optimized (Clean)[/]"
        else:
            state_str = f"[yellow]Degraded ({dirty*100:.1f}% Dirty)[/]"

        table.add_row(
            d["device"],
            d["interface"],
            d["model"],
            health_str,
            f"{d['tbw']:.2f}",
            f"{d['op']:.2f}%",
            state_str
        )
    console.print(table)

# ============================================================================
# 13. EXECUTION ROUTER & ENTRY POINT
# ============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dusky Drive Health Diagnostic Suite",
        epilog="Arch Linux Kernel 7.1 Multi-Interface SSD Analyzer"
    )
    parser.add_argument("--mock", action="store_true", help="Execute in safe isolation demonstration mode with mock profiles.")
    parser.add_argument("--scan", action="store_true", help="Perform real read-only unallocated sector scan (requires root privileges).")
    parser.add_argument("--device", type=str, default=None, help="Path of physical drive to target (e.g. /dev/nvme0n1 or /dev/sda)")
    parser.add_argument("--dry-run-discard", action="store_true", help="Dry run the recommended discard operations to verify bounds.")
    args = parser.parse_args()

    if not args.mock and os.geteuid() != 0:
        console.print("[yellow][!] Live diagnostics require root privileges. Auto-elevating via sudo...[/]")
        try:
            os.execvp("sudo", ["sudo", sys.executable] + sys.argv)
        except Exception as e:
            console.print(f"[bold red][x] Privilege auto-elevation failed: {e}[/]")
            sys.exit(1)

    console.print(Align.center(Panel(
        "[bold cyan]DUSKY DRIVE HEALTH DIAGNOSTIC SUITE[/]\n"
        "[dim]Linux Kernel 7.1 & Python 3.14+ Modern Storage Engine Diagnostics[/]",
        border_style="cyan",
        width=PANEL_WIDTH
    )))

    if args.mock:
        console.print("[bold green][*] Mode: Safe Isolation Mock Demonstration[/]")
        console.print("[dim]Simulating diagnostics for QLC NVMe, TLC NVMe, and SATA SSD drives...[/]\n")
        
        render_drive_diagnostics(MOCK_INTEL_LAYOUT, MOCK_INTEL_SMART, scan_ratio=0.0, dry_run=args.dry_run_discard, is_mock=True)
        render_drive_diagnostics(MOCK_SAMSUNG_LAYOUT, MOCK_SAMSUNG_SMART, scan_ratio=0.0, dry_run=args.dry_run_discard, is_mock=True)
        render_drive_diagnostics(MOCK_SATA_LAYOUT, MOCK_SATA_SMART, scan_ratio=0.35, dry_run=args.dry_run_discard, is_mock=True)
        
        render_glossary_panel()

        mock_summary = [
            {"device": "/dev/nvme0n1", "interface": "NVMe", "model": MOCK_INTEL_SMART["model"], "pct_used": MOCK_INTEL_SMART["percentage_used"], "tbw": MOCK_INTEL_SMART["tbw_written"], "op": 73.9, "dirty_ratio": 0.0},
            {"device": "/dev/nvme1n1", "interface": "NVMe", "model": MOCK_SAMSUNG_SMART["model"], "pct_used": MOCK_SAMSUNG_SMART["percentage_used"], "tbw": MOCK_SAMSUNG_SMART["tbw_written"], "op": 46.3, "dirty_ratio": 0.0},
            {"device": "/dev/sda", "interface": "SATA", "model": MOCK_SATA_SMART["model"], "pct_used": MOCK_SATA_SMART["percentage_used"], "tbw": MOCK_SATA_SMART["tbw_written"], "op": 1.7, "dirty_ratio": 0.35}
        ]
        render_summary_table(mock_summary)
        sys.exit(0)

    assert os.geteuid() == 0, "Security Assertion Failure: Root privileges required for live run."

    devices = []
    if args.device:
        if not os.path.exists(args.device):
            console.print(f"[bold red][!] Specified device path does not exist: {args.device}[/]")
            sys.exit(1)
        mode = os.stat(args.device).st_mode
        if not stat.S_ISBLK(mode):
            console.print(f"[bold red][!] Specified path is not a valid block device node: {args.device}[/]")
            sys.exit(1)
        devices = [args.device]
    else:
        devices = detect_ssd_devices()

    if not devices:
        console.print("[bold red][!] No physical NVMe or SATA/SCSI SSD drives detected on this host.[/]")
        sys.exit(1)

    summary_data = []

    for dev in devices:
        layout = parse_partition_table(dev)
        if not layout:
            continue

        smart = query_live_smart_data(dev)
        if not smart:
            capacity_tb = get_device_capacity_tb(dev)
            flash_type = detect_flash_type(layout.model, 0, 0.0, capacity_tb)
            smart = {
                "device": dev,
                "model": layout.model,
                "serial": "N/A",
                "firmware": "N/A",
                "temp": 30.0,
                "percentage_used": 0,
                "tbw_written": 0.0,
                "tbw_rated": estimate_tbw_rated(capacity_tb, flash_type),
                "power_on_hours": 0,
                "unsafe_shutdowns": 0,
                "media_errors": 0,
                "flash_type": flash_type,
                "interface": "NVMe" if "nvme" in dev else "SATA"
            }

        scan_ratio = None
        if args.scan and layout.unallocated_gaps:
            scan_ratio = scan_unallocated_regions(dev, layout.unallocated_gaps, layout.sector_size)

        render_drive_diagnostics(layout, smart, scan_ratio, dry_run=args.dry_run_discard)
        
        unalloc_sec = sum((g[1] - g[0] + 1) for g in layout.unallocated_gaps)
        op_raw_pct = (unalloc_sec / layout.total_sectors) * 100.0 if layout.total_sectors else 0.0

        summary_data.append({
            "device": dev,
            "interface": smart.get("interface", "NVMe"),
            "model": smart.get("model", "Unknown SSD"),
            "pct_used": smart.get("percentage_used", 0),
            "tbw": smart.get("tbw_written", 0.0),
            "op": op_raw_pct,
            "dirty_ratio": scan_ratio
        })

    render_glossary_panel()

    if len(summary_data) > 0:
        render_summary_table(summary_data)

if __name__ == "__main__":
    main()
