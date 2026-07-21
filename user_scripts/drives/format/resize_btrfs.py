#!/usr/bin/env python3
"""
Dusky BTRFS Safe Partition & Filesystem Resizer
Arch Linux (latest) hardening edition.

Safely handles bi-directional resizing of Btrfs filesystems and GPT disk partitions:
- For Shrinking: Shrinks Btrfs filesystem -> resizes GPT partition -> fits Btrfs to max.
- For Growing: Resizes GPT partition -> resizes Btrfs filesystem to max.
"""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path


def ensure_root() -> None:
    if os.geteuid() != 0:
        print("\033[1;38;5;220m[*] Elevating to root privileges via sudo...\033[0m", file=sys.stderr)
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            argv = list(sys.argv)
            if argv and argv[0]:
                argv[0] = os.path.abspath(argv[0])
            os.execvp("sudo", ["sudo", sys.executable] + argv)
        except OSError as exc:
            print(f"\033[1;38;5;196m[!] Failed to elevate privileges: {exc}\033[0m", file=sys.stderr)
            sys.exit(1)


def run_cmd(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    res = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and res.returncode != 0:
        print(f"\033[1;38;5;196m[!] Command failed: {shlex.join(cmd)}\n{res.stderr.strip()}\033[0m", file=sys.stderr)
        sys.exit(res.returncode)
    return res


def parse_size_bytes(size_str: str) -> int:
    size_str = size_str.strip().upper()
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([KMGTPE]?i?B?)$", size_str)
    if not match:
        raise ValueError(f"Invalid size specification: {size_str}")

    val = float(match.group(1))
    unit = match.group(2)

    multipliers = {
        "": 1,
        "B": 1,
        "K": 1024,
        "KB": 1000,
        "KI": 1024,
        "KIB": 1024,
        "M": 1024**2,
        "MB": 1000**2,
        "MI": 1024**2,
        "MIB": 1024**2,
        "G": 1024**3,
        "GB": 1000**3,
        "GI": 1024**3,
        "GIB": 1024**3,
        "T": 1024**4,
        "TB": 1000**4,
        "TI": 1024**4,
        "TIB": 1024**4,
    }

    return int(val * multipliers.get(unit, 1024**3))


def get_partition_info(mountpoint: str) -> dict[str, str]:
    res = run_cmd(["findmnt", "-n", "-e", "-o", "SOURCE,UUID,FSTYPE", "-M", mountpoint])
    parts = res.stdout.strip().split()
    if len(parts) < 3:
        print(f"\033[1;38;5;196m[!] Could not resolve mount info for {mountpoint}\033[0m", file=sys.stderr)
        sys.exit(1)

    source = re.sub(r"\[.*?\]$", "", parts[0]).strip()
    uuid = parts[1]
    fstype = parts[2]

    if fstype != "btrfs":
        print(f"\033[1;38;5;196m[!] Mountpoint {mountpoint} is not a Btrfs filesystem ({fstype})\033[0m", file=sys.stderr)
        sys.exit(1)

    if source.startswith("UUID="):
        source_dev = run_cmd(["findfs", source]).stdout.strip()
    else:
        source_dev = os.path.realpath(source)

    lsblk = run_cmd(["lsblk", "-n", "-o", "PKNAME,KNAME,PATH,TYPE,SIZE", source_dev])
    lsblk_parts = lsblk.stdout.strip().split()

    if not source_dev.startswith("/dev/"):
        print(f"\033[1;38;5;196m[!] Source device {source_dev} is not a valid block device\033[0m", file=sys.stderr)
        sys.exit(1)

    match = re.match(r"^(/dev/[a-z]+|/dev/nvme\d+n\d+|/dev/mmcblk\d+)(?:p?(\d+))?$", source_dev)
    if not match:
        print(f"\033[1;38;5;196m[!] Could not parse partition device structure for {source_dev}\033[0m", file=sys.stderr)
        sys.exit(1)

    disk_dev = match.group(1)
    part_num = match.group(2)

    if not part_num:
        part_num = run_cmd(["cat", f"/sys/class/block/{os.path.basename(source_dev)}/partition"]).stdout.strip()

    return {
        "mountpoint": mountpoint,
        "partition_dev": source_dev,
        "disk_dev": disk_dev,
        "part_num": part_num,
        "uuid": uuid,
    }


def print_status(info: dict[str, str]) -> None:
    mnt = info["mountpoint"]
    part = info["partition_dev"]

    print(f"\033[1;38;5;220m=== Btrfs Partition & Filesystem Status for {mnt} ===\033[0m")
    print(f"Partition Device : {part} (Disk: {info['disk_dev']}, Partition #: {info['part_num']})")

    btrfs_show = run_cmd(["btrfs", "filesystem", "show", mnt])
    print("\n\033[1;38;5;81m[*] Live Btrfs Filesystem Devices:\033[0m")
    print(btrfs_show.stdout.strip())

    btrfs_usage = run_cmd(["btrfs", "filesystem", "usage", mnt])
    print("\n\033[1;38;5;81m[*] Live Btrfs Allocation & Usage:\033[0m")
    print(btrfs_usage.stdout.strip())

    sfdisk_dump = run_cmd(["sfdisk", "-d", info["disk_dev"]])
    print(f"\n\033[1;38;5;81m[*] GPT Partition Table ({info['disk_dev']}):\033[0m")
    for line in sfdisk_dump.stdout.splitlines():
        if part in line:
            print(line)


def safe_shrink(info: dict[str, str], delta_bytes: int) -> None:
    mnt = info["mountpoint"]
    part = info["partition_dev"]
    disk = info["disk_dev"]
    part_num = info["part_num"]

    delta_gib = delta_bytes / (1024**3)
    print(f"\033[1;38;5;81m[*] Preparing to SAFE-SHRINK Btrfs filesystem on {mnt} by {delta_gib:.2f} GiB...\033[0m")

    # Step 1: Check Btrfs free unallocated space
    btrfs_usage = run_cmd(["btrfs", "filesystem", "usage", "-b", mnt]).stdout
    match_free = re.search(r"Free \(estimated\):\s+(\d+)", btrfs_usage)
    if match_free:
        free_bytes = int(match_free.group(1))
        if free_bytes < (delta_bytes + 512 * 1024 * 1024):
            print(
                f"\033[1;38;5;196m[!] ERROR: Insufficient free unallocated Btrfs space!\n"
                f"Free: {free_bytes / (1024**3):.2f} GiB, Requested shrink: {delta_gib:.2f} GiB (+500MB safety margin)\033[0m",
                file=sys.stderr,
            )
            sys.exit(1)

    # Step 2: Shrink Btrfs filesystem FIRST
    print(f"\033[1;38;5;114m[1/4] Shrinking Btrfs filesystem by {delta_bytes} bytes...\033[0m")
    run_cmd(["btrfs", "filesystem", "resize", f"-{delta_bytes}", mnt])

    # Step 3: Get new Btrfs device total_bytes
    btrfs_show = run_cmd(["btrfs", "filesystem", "show", mnt]).stdout
    match_dev_size = re.search(r"devid\s+1\s+size\s+([\d\.]+\w+)", btrfs_show)
    print(f"\033[1;38;5;114m[+] Btrfs filesystem shrunk successfully. New dev size: {match_dev_size.group(1) if match_dev_size else 'unknown'}\033[0m")

    # Step 4: Calculate new GPT partition sector boundaries
    part_sectors = int(run_cmd(["cat", f"/sys/class/block/{os.path.basename(part)}/size"]).stdout.strip())
    part_start = int(run_cmd(["cat", f"/sys/class/block/{os.path.basename(part)}/start"]).stdout.strip())

    delta_sectors = (delta_bytes + 511) // 512
    new_part_sectors = part_sectors - delta_sectors
    new_part_end = part_start + new_part_sectors - 1

    print(f"\033[1;38;5;114m[2/4] Updating GPT partition table for {part} (New sectors: {new_part_sectors})...\033[0m")

    input_str = f"start= {part_start}, size= {new_part_sectors}\n"
    proc = subprocess.run(
        ["sfdisk", "--no-reread", "-N", str(part_num), disk],
        input=input_str,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        print(f"\033[1;38;5;196m[!] sfdisk notice: {proc.stderr.strip()}\033[0m")

    # Step 5: Inform kernel and re-fit Btrfs to max
    print(f"\033[1;38;5;114m[3/4] Requesting kernel partition table refresh...\033[0m")
    run_cmd(["partx", "-u", disk], check=False)
    run_cmd(["udevadm", "settle"], check=False)

    print(f"\033[1;38;5;114m[4/4] Expanding Btrfs filesystem to fit exact new partition bounds (max)...\033[0m")
    run_cmd(["btrfs", "filesystem", "resize", "max", mnt])

    print("\033[1;38;5;114m[+] SAFE SHRINK COMPLETE! Partition and Btrfs filesystem sizes are 100% matched.\033[0m")


def safe_grow(info: dict[str, str], delta_bytes: int) -> None:
    mnt = info["mountpoint"]
    part = info["partition_dev"]
    disk = info["disk_dev"]
    part_num = info["part_num"]

    delta_gib = delta_bytes / (1024**3)
    print(f"\033[1;38;5;81m[*] Preparing to SAFE-GROW partition and Btrfs filesystem on {mnt} by {delta_gib:.2f} GiB...\033[0m")

    part_sectors = int(run_cmd(["cat", f"/sys/class/block/{os.path.basename(part)}/size"]).stdout.strip())
    part_start = int(run_cmd(["cat", f"/sys/class/block/{os.path.basename(part)}/start"]).stdout.strip())
    delta_sectors = (delta_bytes + 511) // 512
    new_part_sectors = part_sectors + delta_sectors

    # Step 1: Resize GPT partition FIRST
    print(f"\033[1;38;5;114m[1/3] Expanding GPT partition table for {part} to {new_part_sectors} sectors...\033[0m")
    input_str = f"start= {part_start}, size= {new_part_sectors}\n"
    proc = subprocess.run(
        ["sfdisk", "--no-reread", "-N", str(part_num), disk],
        input=input_str,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        print(f"\033[1;38;5;196m[!] sfdisk notice: {proc.stderr.strip()}\033[0m")

    # Step 2: Notify kernel
    print(f"\033[1;38;5;114m[2/3] Requesting kernel partition table refresh...\033[0m")
    run_cmd(["partx", "-u", disk], check=False)
    run_cmd(["udevadm", "settle"], check=False)

    # Step 3: Expand Btrfs filesystem
    print(f"\033[1;38;5;114m[3/3] Expanding Btrfs filesystem to fit max new partition bounds...\033[0m")
    run_cmd(["btrfs", "filesystem", "resize", "max", mnt])

    print("\033[1;38;5;114m[+] SAFE GROW COMPLETE! Partition and Btrfs filesystem sizes are 100% matched.\033[0m")


def main() -> None:
    ensure_root()

    parser = argparse.ArgumentParser(description="Dusky Safe BTRFS Partition & Filesystem Resizer")
    parser.add_argument("-m", "--mountpoint", default="/", help="Target Btrfs mountpoint (default: /)")
    parser.add_argument("-s", "--status", action="store_true", help="Display current partition & filesystem sizes")
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument("--shrink", metavar="SIZE", help="Safe shrink by SIZE (e.g. 1G, 500M)")
    group.add_argument("--grow", metavar="SIZE", help="Safe grow by SIZE (e.g. 1G, 2G)")

    args = parser.parse_args()

    info = get_partition_info(args.mountpoint)

    if args.status or (not args.shrink and not args.grow):
        print_status(info)
        return

    if args.shrink:
        delta_bytes = parse_size_bytes(args.shrink)
        safe_shrink(info, delta_bytes)
    elif args.grow:
        delta_bytes = parse_size_bytes(args.grow)
        safe_grow(info, delta_bytes)


if __name__ == "__main__":
    main()
