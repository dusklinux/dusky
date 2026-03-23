#!/usr/bin/env python3
"""
Advanced Btrfs/Snapper Flat Layout Manager (snapctl)
Designed for Arch Linux flat Btrfs topologies.
Evaluates top-level trees to perform safer staged subvolume replacements,
bypassing OverlayFS abstractions. Includes strict nested subvolume guardrails.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import typing as t
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


def run_cmd(cmd: list[str], check: bool = True) -> str:
    """Executes a shell command and returns the stripped stdout."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"[!] Command failed: {' '.join(cmd)}\n{result.stderr}", file=sys.stderr)
        sys.exit(result.returncode)
    return result.stdout.strip()


def get_btrfs_device(mountpoint: str) -> str:
    """Finds the underlying physical block device, evaluating UUIDs/OverlayFS."""
    cmd = ["findmnt", "--fstab", "--evaluate", "-n", "-o", "SOURCE", mountpoint]
    device = run_cmd(cmd)
    if not device.startswith("/dev/"):
        sys.exit(f"[!] Fatal: Could not resolve physical block device for {mountpoint}. Found: {device}")
    return device


def get_subvol_from_fstab(mountpoint: str) -> str:
    """Extracts the exact subvolume name from fstab options."""
    options = run_cmd(["findmnt", "--fstab", "-n", "-o", "OPTIONS", mountpoint])
    match = re.search(r"subvol=([^,]+)", options)
    if not match:
        sys.exit(f"[!] Fatal: No 'subvol=' option found in fstab for {mountpoint}.")
    return match.group(1).lstrip("/")


def validate_snapshot_id(snap_id: str) -> str:
    """Ensures a snapshot ID is a strictly numeric snapper ID."""
    if not snap_id.isdigit():
        sys.exit(f"[!] Fatal: Invalid snapshot ID: {snap_id!r}")
    return snap_id


@contextmanager
def mount_top_level(device: str) -> t.Iterator[Path]:
    """Context manager to handle a temporary, collision-free top-level Btrfs mount."""
    with tempfile.TemporaryDirectory(prefix="btrfs_top_level_mgmt_", dir="/mnt") as tmpdir:
        mnt_point = Path(tmpdir)
        mounted = False

        print(f"[*] Mounting top-level tree (subvolid=5) for {device}...", file=sys.stderr)
        run_cmd(["mount", "-o", "subvolid=5", device, str(mnt_point)])
        mounted = True
        try:
            yield mnt_point
        finally:
            if mounted:
                print("[*] Unmounting top-level tree...", file=sys.stderr)
                run_cmd(["umount", str(mnt_point)])


def handle_list(config: str, as_json: bool) -> None:
    """Outputs the snapshot list either as raw stdout or structured JSON for GUIs."""
    if not as_json:
        result = subprocess.run(["snapper", "-c", config, "list"])
        sys.exit(result.returncode)

    # Fetch raw data, strip the two header lines
    raw_list = run_cmd(["snapper", "-c", config, "list", "--disable-used-space"]).splitlines()[2:]
    
    gui_data = []
    for line in raw_list:
        # Support BOTH ASCII '|' and Unicode box drawing '│' separators dynamically
        parts = [p.strip() for p in re.split(r'[|│]', line)]
        
        if len(parts) >= 7:
            snap_id = parts[0]
            
            # Skip the currently running state (ID 0)
            if snap_id == "0" or not snap_id.isdigit():
                continue

            gui_data.append({
                "id": snap_id,
                "type": parts[1],
                "date": parts[3], 
                "description": parts[6]
            })
            
    # Output purely the JSON array.
    print(json.dumps(gui_data))


def handle_create(config: str, description: str) -> None:
    """Creates a new snapshot for the target config."""
    print(f"[*] Creating snapshot for '{config}': {description}")
    run_cmd(["snapper", "-c", config, "create", "-d", description])
    print("[+] Snapshot created successfully.")


def handle_restore(config: str, snap_id: str) -> None:
    """Performs a safer staged subvolume replacement to restore a snapshot."""
    snap_id = validate_snapshot_id(snap_id)

    # 1. Map config to its mountpoint
    config_out = run_cmd(["snapper", "-c", config, "get-config"])
    target_mnt = ""
    for line in config_out.splitlines():
        if line.startswith("SUBVOLUME"):
            target_mnt = line.split("|")[-1].strip()
            break

    if not target_mnt:
        sys.exit(f"[!] Fatal: Could not determine SUBVOLUME for snapper config '{config}'.")

    snapshots_mnt = f"{target_mnt}/.snapshots" if target_mnt != "/" else "/.snapshots"

    # 2. Get layout details from fstab
    device = get_btrfs_device(target_mnt)
    active_subvol = get_subvol_from_fstab(target_mnt)
    snapshots_subvol = get_subvol_from_fstab(snapshots_mnt)

    # 3. Mount top-level and execute replacement
    with mount_top_level(device) as top_mnt:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        source_snapshot = top_mnt / snapshots_subvol / snap_id / "snapshot"
        target_path = top_mnt / active_subvol
        backup_path = top_mnt / f"{active_subvol}_backup_{timestamp}"
        staging_path = top_mnt / f"{active_subvol}_restore_{snap_id}_{timestamp}"

        if not source_snapshot.is_dir():
            sys.exit(f"[!] Fatal: Snapshot ID {snap_id} does not exist at {source_snapshot}")

        # --- CRITICAL SAFETY GUARDRAIL ---
        nested_result = subprocess.run(
            ["btrfs", "subvolume", "list", "-o", str(target_path)],
            capture_output=True,
            text=True,
        )
        if nested_result.returncode != 0:
            detail = nested_result.stderr.strip() or nested_result.stdout.strip() or "unknown error"
            sys.exit(f"[!] Fatal: Failed to inspect nested subvolumes inside '{active_subvol}'.\n{detail}")

        nested_check = nested_result.stdout.strip()
        if nested_check:
            print(f"\n[!] CRITICAL HALT: Nested subvolumes detected physically inside '{active_subvol}'!", file=sys.stderr)
            print(f"Offending subvolumes:\n{nested_check}\n", file=sys.stderr)
            print("[!] An atomic rollback would trap these inside the backup subvolume.", file=sys.stderr)
            print("[!] Aborting restore to prevent data loss. Please flatten your Btrfs topology first.", file=sys.stderr)
            sys.exit(1)
        # ---------------------------------

        print(f"[*] Creating staged restore subvolume {staging_path.name}...")
        run_cmd(["btrfs", "subvolume", "snapshot", str(source_snapshot), str(staging_path)])

        moved_active = False
        try:
            print(f"[*] Moving active subvolume to {backup_path.name}...")
            target_path.rename(backup_path)
            moved_active = True

            print(f"[*] Activating restored snapshot as {target_path.name}...")
            staging_path.rename(target_path)
        except OSError as exc:
            if moved_active and not target_path.exists() and backup_path.exists():
                try:
                    print("[!] Restore activation failed; attempting rapid rollback...", file=sys.stderr)
                    backup_path.rename(target_path)
                    if staging_path.exists():
                        run_cmd(["btrfs", "subvolume", "delete", str(staging_path)], check=False)
                except OSError as rollback_exc:
                    sys.exit(
                        "[!] Fatal: Restore failed and automatic rollback also failed.\n"
                        f"Restore error: {exc}\nRollback error: {rollback_exc}"
                    )
                sys.exit(f"[!] Fatal: Restore failed before activation. Rolled back to active successfully.\n{exc}")

            if not moved_active and staging_path.exists():
                run_cmd(["btrfs", "subvolume", "delete", str(staging_path)], check=False)

            sys.exit(f"[!] Fatal: Restore failed.\n{exc}")

    print("\n[+] Restoration complete.")

    # 4. Handle runtime state
    if target_mnt == "/":
        print("\n[!] ROOT FILESYSTEM RESTORED. You MUST reboot immediately for changes to take effect.")
    else:
        print(f"[*] Hot-reloading {target_mnt}...")
        run_cmd(["umount", "-l", target_mnt], check=False)
        run_cmd(["mount", target_mnt])
        print(f"[+] {target_mnt} successfully remounted.")


def main() -> None:
    if os.geteuid() != 0:
        sys.exit("[!] This script requires root privileges. Please run with sudo.")

    parser = argparse.ArgumentParser(
        description="Advanced Snapper Flat-Layout Manager for Arch Linux",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument("-c", "--config", required=True, help="Target Snapper configuration (e.g., root, home)")
    parser.add_argument("--json", action="store_true", help="Format list output as JSON for GUI ingestion")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("-l", "--list", action="store_true", help="List snapshots for the configuration")
    group.add_argument("-C", "--create", metavar="DESC", help="Create a new snapshot with a description")
    group.add_argument("-R", "--restore", metavar="ID", help="Restore subvolume to the specified snapshot ID")

    args = parser.parse_args()

    match args:
        case args if args.list:
            handle_list(args.config, args.json)
        case args if args.create:
            handle_create(args.config, args.create)
        case args if args.restore:
            handle_restore(args.config, args.restore)


if __name__ == "__main__":
    main()
