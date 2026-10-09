#!/usr/bin/env python3
"""Resize a mounted, single-device Btrfs filesystem on a direct GPT partition.

Shrink the filesystem before its partition; grow the partition before the
filesystem. Never grow the filesystem until on-disk and kernel bounds agree.
"""

import argparse
from datetime import UTC, datetime
from decimal import Decimal
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from typing import Any


def ensure_root() -> None:
    if os.geteuid() != 0:
        sys.stdout.flush()
        sys.stderr.flush()
        argv = [os.path.abspath(sys.argv[0]), *sys.argv[1:]]
        os.execvp("sudo", ["sudo", sys.executable, *argv])


def run_cmd(cmd: list[str], *, input_data: str | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, input=input_data, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env={**os.environ, "LC_ALL": "C"})
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Command failed ({result.returncode}): {shlex.join(cmd)}\n{detail}")
    return result


def parse_size_bytes(size_str: str) -> int:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([KMGTPE]?)(I?)(B?)", size_str.strip().upper())
    if not match or (not match[2] and match[3]):
        raise ValueError(f"Invalid size specification: {size_str}")
    power = "KMGTPE".index(match[2]) + 1 if match[2] else 0
    base = 1000 if match[4] and not match[3] else 1024
    # Decimal multiplication can silently round at the current context precision.
    numerator, denominator = Decimal(match[1]).as_integer_ratio()
    size, remainder = divmod(numerator * base**power, denominator)
    if remainder or size <= 0:
        raise ValueError("Size must be a positive whole number of bytes")
    return size


def filesystem_device(mountpoint: str) -> tuple[int, int, str]:
    """The documented raw output gives exact bytes and the actual device ID."""
    output = run_cmd(["btrfs", "filesystem", "show", "--raw", mountpoint]).stdout
    total = re.search(r"Total devices\s+(\d+)", output)
    devices = re.findall(r"^\s*devid\s+(\d+)\s+size\s+(\d+)\s+used\s+\d+\s+path\s+(.+)$",
                         output, re.MULTILINE)
    if not total or int(total[1]) != 1 or len(devices) != 1:
        raise ValueError("Only complete, single-device Btrfs filesystems are supported")
    devid, size, path = devices[0]
    return int(devid), int(size), os.path.realpath(path)


def partition_table(disk: str) -> frozendict[str, Any]:
    table = json.loads(run_cmd(["sfdisk", "--lock=no", "--json", disk]).stdout,
                       object_pairs_hook=frozendict, array_hook=tuple)["partitiontable"]
    if table["label"] != "gpt" or table["unit"] != "sectors":
        raise ValueError("Only GPT partition tables are supported")
    return table


def get_partition_info(mountpoint: str) -> dict[str, Any]:
    mountpoint = os.path.realpath(mountpoint)
    mounts = json.loads(run_cmd(["findmnt", "--json", "--evaluate", "--nofsroot",
                                "--mountpoint", mountpoint,
                                "--output", "SOURCE,FSTYPE,OPTIONS"]).stdout)["filesystems"]
    if len(mounts) != 1 or mounts[0]["fstype"] != "btrfs":
        raise ValueError(f"{mountpoint} must be a Btrfs mountpoint")
    devid, fs_bytes, source = filesystem_device(mountpoint)
    mount_source = os.path.realpath(mounts[0]["source"])
    if source != mount_source:
        raise ValueError("Mount source and Btrfs device do not agree")
    if not Path(source).is_block_device():
        raise ValueError(f"Not a block device: {source}")
    nodes = json.loads(run_cmd(["lsblk", "--json", "--nodeps", "--paths", "--bytes", "--output",
                               "PATH,TYPE,PKNAME,SIZE", source]).stdout)["blockdevices"]
    if len(nodes) != 1 or nodes[0]["type"] != "part" or not nodes[0]["pkname"]:
        raise ValueError("Btrfs must reside directly on a disk partition (no LUKS/LVM/RAID layers)")
    disk = os.path.realpath(nodes[0]["pkname"])
    if any((Path("/sys/class/block") / Path(disk).name / "slaves").iterdir()):
        raise ValueError("Partitions on stacked devices (LUKS/LVM/RAID) are not supported")
    part_num = int((Path("/sys/class/block") / Path(source).name / "partition").read_text(encoding="ascii"))
    table = partition_table(disk)
    partitions = [p for p in table["partitions"] if os.path.realpath(p["node"]) == source]
    if len(partitions) != 1:
        raise ValueError("Partition not found uniquely in GPT table")
    partition = partitions[0]
    sector_size = int(table["sectorsize"])
    if int(partition["size"]) * sector_size != int(nodes[0]["size"]):
        raise ValueError("Kernel and GPT partition sizes disagree; reconcile them before resizing")
    start_bytes = int((Path("/sys/class/block") / Path(source).name / "start").read_text(encoding="ascii")) * 512
    if start_bytes != int(partition["start"]) * sector_size:
        raise ValueError("Kernel and GPT partition starts disagree")
    if fs_bytes > int(nodes[0]["size"]):
        raise ValueError("Filesystem extends beyond its partition")
    superblock = run_cmd(["btrfs", "inspect-internal", "dump-super", source]).stdout
    fs_sector = re.search(r"^sectorsize\s+(\d+)$", superblock, re.MULTILINE)
    if not fs_sector:
        raise ValueError("Cannot determine Btrfs sector size")
    return {
        "rw": "rw" in mounts[0]["options"].split(","),
        "fs_sector": int(fs_sector[1]), "mountpoint": mountpoint,
        "partition_dev": source, "disk_dev": disk, "part_num": part_num,
        "devid": devid, "fs_bytes": fs_bytes, "sector_size": sector_size,
        "partition": partition, "table": table,
    }


def print_status(info: dict[str, Any]) -> None:
    print(f"Partition: {info['partition_dev']} (disk {info['disk_dev']}, #{info['part_num']})")
    print(f"Logical sector size: {info['sector_size']} bytes")
    print(f"Partition size: {info['partition']['size'] * info['sector_size']} bytes")
    print(f"Btrfs device {info['devid']} size: {info['fs_bytes']} bytes")
    print(run_cmd(["btrfs", "filesystem", "usage", info["mountpoint"]]).stdout.rstrip())


def resize(info: dict[str, Any], delta_bytes: int, *, shrink: bool) -> None:
    # Use the same BSD disk lock as sfdisk/udev, for the entire operation.
    # Re-read under the lock: another resize may have finished while we waited.
    with open(info["disk_dev"], "rb") as disk_file:
        fcntl.flock(disk_file, fcntl.LOCK_EX)
        current = get_partition_info(info["mountpoint"])
        if (current["disk_dev"], current["partition_dev"], current["devid"]) != (
                info["disk_dev"], info["partition_dev"], info["devid"]):
            raise RuntimeError("Target device changed while acquiring the disk lock")
        resize_locked(current, delta_bytes, shrink=shrink)


def resize_locked(info: dict[str, Any], delta_bytes: int, *, shrink: bool) -> None:
    """Resize while the caller holds the exclusive disk lock."""
    if not info["rw"]:
        raise ValueError("Btrfs must be mounted read-write for resizing")
    disk, part, mnt = info["disk_dev"], info["partition_dev"], info["mountpoint"]
    sector = info["sector_size"]
    if delta_bytes <= 0 or delta_bytes % sector:
        raise ValueError(f"Resize amount must be a positive multiple of the {sector}-byte logical sector")
    partition = info["partition"]
    new_sectors = int(partition["size"]) + (-1 if shrink else 1) * (delta_bytes // sector)
    if new_sectors <= 0:
        raise ValueError("Shrink would remove the entire partition")
    end = int(partition["start"]) + new_sectors  # Exclusive end.
    limit = int(info["table"]["lastlba"]) + 1
    for other in info["table"]["partitions"]:
        if int(other["start"]) > int(partition["start"]):
            limit = min(limit, int(other["start"]))
    if end > limit:
        raise ValueError("Growth would overlap another partition or the GPT backup table")
    new_bytes = new_sectors * sector
    spec = f"start={partition['start']}, size={new_sectors}\n"
    # The parent already holds the lock; taking it again would deadlock.
    cmd = ["sfdisk", "--lock=no", "--no-reread", "--no-tell-kernel",
           "--wipe", "never", "--wipe-partitions", "never", "-N", str(info["part_num"]), disk]
    run_cmd([*cmd[:-1], "--no-act", disk], input_data=spec)
    # Save a reviewable recovery table before the first mutation.
    state_home = os.environ.get("XDG_STATE_HOME", "")
    backup = (Path(state_home) if os.path.isabs(state_home) else Path.home() / ".local/state") / "dusky/btrfs-resize"
    new_directory_parents = []
    directory = backup
    while not directory.exists():
        directory = directory.parent
        new_directory_parents.append(directory)
    backup.mkdir(parents=True, exist_ok=True)
    backup = backup / f"{Path(disk).name}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}.sfdisk"
    with backup.open("x", encoding="utf-8") as recovery:
        recovery.write(run_cmd(["sfdisk", "--lock=no", "--dump", disk]).stdout)
        recovery.flush()
        os.fsync(recovery.fileno())
    # Persist the filename and any newly created directory entries as well.
    for directory in (backup.parent, *new_directory_parents):
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    print(f"Original partition table saved to {backup}", flush=True)
    print(f"Resizing partition to {new_bytes} bytes ({'shrink' if shrink else 'grow'})", flush=True)
    if shrink:
        # Absolute size avoids shrinking an already partially sized filesystem
        # by the wrong amount. Btrfs itself checks and relocates allocated data.
        if info["fs_bytes"] > new_bytes:
            run_cmd(["btrfs", "filesystem", "resize", f"{info['devid']}:{new_bytes}", mnt])
        devid, actual, source = filesystem_device(mnt)
        if devid != info["devid"] or source != part or actual > new_bytes:
            raise RuntimeError("Cannot verify filesystem fits; partition has not been shrunk")
    run_cmd(cmd, input_data=spec)
    written = partition_table(disk)
    expected = dict(info["table"])
    expected["partitions"] = tuple(dict(p, size=new_sectors) if p == partition else p
                                   for p in info["table"]["partitions"])
    if written != expected:
        raise RuntimeError("GPT update did not preserve the table with only the requested size change; stopping")
    run_cmd(["partx", "--update", "--nr", str(info["part_num"]), disk])
    # partx updates the kernel synchronously; blockdev/sysfs do not need udev.
    kernel_bytes = int(run_cmd(["blockdev", "--getsize64", part]).stdout)
    kernel_start = int((Path("/sys/class/block") / Path(part).name / "start").read_text(encoding="ascii")) * 512
    if kernel_bytes != new_bytes or kernel_start != int(partition["start"]) * sector:
        raise RuntimeError("Kernel partition bounds disagree with GPT; filesystem will not be expanded")
    run_cmd(["btrfs", "filesystem", "resize", f"{info['devid']}:max", mnt])
    devid, actual, source = filesystem_device(mnt)
    # Btrfs rounds device size down to a filesystem-sector boundary.
    if devid != info["devid"] or source != part or actual != new_bytes // info["fs_sector"] * info["fs_sector"]:
        raise RuntimeError("Final filesystem size does not fit the updated partition as expected")
    print(f"Resize complete: partition {new_bytes} bytes, Btrfs {actual} bytes.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-m", "--mountpoint", default="/", help="Target Btrfs mountpoint (default: /)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-s", "--status", action="store_true", help="Display partition and filesystem sizes")
    group.add_argument("--shrink", type=parse_size_bytes, metavar="SIZE", help="Shrink partition by SIZE (e.g. 1GiB)")
    group.add_argument("--grow", type=parse_size_bytes, metavar="SIZE", help="Grow partition by SIZE (e.g. 500MiB)")
    parser.epilog = "Sizes: bytes or K/M/G/T/P/E (binary); KB/MB/... (decimal); KiB/MiB/... (binary)."
    args = parser.parse_args()
    for binary in ("findmnt", "lsblk", "sfdisk", "btrfs", "partx", "blockdev"):
        if not shutil.which(binary):
            parser.error(f"Missing required tool: {binary}")
    ensure_root()
    info = get_partition_info(args.mountpoint)
    if args.shrink is None and args.grow is None:
        print_status(info)
    else:
        resize(info, args.shrink if args.shrink is not None else args.grow, shrink=args.shrink is not None)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"Resize failed: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Resize interrupted; inspect filesystem, GPT, and kernel sizes before retrying.", file=sys.stderr)
        sys.exit(130)
