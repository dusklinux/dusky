#!/usr/bin/env python3
"""
Advanced Btrfs/Snapper Flat Layout Manager (snapctl)
Engineered for strict safety, coordinated subvolume swapping, and interactive TUI on Arch Linux.
"""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def ensure_root() -> None:
    """Seamlessly auto-elevate to root via sudo if run as a normal user."""
    if os.geteuid() != 0:
        print("\033[1;38;5;220m[*] Elevating to root privileges via sudo...\033[0m", file=sys.stderr)
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            os.execvp("sudo", ["sudo", sys.executable] + sys.argv)
        except OSError as exc:
            fail(f"[!] Failed to elevate privileges: {exc}")


def fail(message: str, exit_code: int = 1) -> None:
    print(f"\033[1;38;5;196m{message}\033[0m", file=sys.stderr)
    sys.exit(exit_code)


def error_text(result: subprocess.CompletedProcess[str]) -> str:
    return result.stderr.strip() or result.stdout.strip() or "<no error output>"


def run_cmd(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        fail(f"[!] Command execution failed: {shlex.join(cmd)}\n{exc}")

    if check and result.returncode != 0:
        fail(f"[!] Command failed: {shlex.join(cmd)}\n{error_text(result)}", result.returncode)

    return result


def run_cmd_raise(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise RuntimeError(f"Command execution failed: {shlex.join(cmd)}\n{exc}") from exc

    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {shlex.join(cmd)}\n{error_text(result)}")

    return result


def run_passthrough(cmd: list[str]) -> int:
    try:
        return subprocess.run(cmd).returncode
    except OSError as exc:
        fail(f"[!] Command execution failed: {shlex.join(cmd)}\n{exc}")
        return 1


def get_btrfs_device(mountpoint: str) -> str:
    # Use live mount info (-M) instead of fstab to natively support LUKS2 and LVM.
    # The -v (--nofsroot) flag cleanly strips the Btrfs subvolume brackets (e.g., [/var/log]).
    # The -e (--evaluate) flag safely resolves UUIDs/labels to raw block device paths.
    result = run_cmd(["findmnt", "-n", "-v", "-e", "-o", "SOURCE", "-M", mountpoint])
    device = result.stdout.strip()
    
    if not device.startswith("/dev/"):
        fail(f"[!] Fatal: Could not resolve physical block device for {mountpoint}. Found: {device}")
    
    return os.path.realpath(device)


def get_active_subvol(mountpoint: str) -> str:
    """
    Bulletproof resolution of the active subvolume name mirroring bash script fail-safes.
    Accounts for edge cases like subvolid= usage or absent fstab entries.
    """
    # 1. Try fstab first (Source of truth for boot)
    result = run_cmd(["findmnt", "--fstab", "-n", "-o", "OPTIONS", "-M", mountpoint], check=False)
    if result.returncode == 0:
        match = re.search(r"(?:^|,)subvol=([^,]+)(?:,|$)", result.stdout.strip())
        if match:
            return match.group(1).lstrip("/")

    # 2. Try live mount info (Crucial if mounted manually or via systemd mount units without fstab)
    result = run_cmd(["findmnt", "-n", "-o", "OPTIONS", "-M", mountpoint], check=False)
    if result.returncode == 0:
        match = re.search(r"(?:^|,)subvol=([^,]+)(?:,|$)", result.stdout.strip())
        if match:
            return match.group(1).lstrip("/")

    # 3. Fallback to native btrfs tools (Ultimate fallback if mounted purely by subvolid)
    result = run_cmd(["btrfs", "subvolume", "show", mountpoint], check=False)
    if result.returncode == 0:
        match = re.search(r"^[ \t]*Path:[ \t]*(.+)$", result.stdout, re.MULTILINE)
        if match:
            path = match.group(1).strip().lstrip("/")
            if path and path not in ("<FS_TREE>", ""):
                return path

    fail(f"[!] Fatal: Could not determine active Btrfs subvolume path for {mountpoint}. No 'subvol=' option found.")


def get_target_mount_from_snapper_config(config: str) -> str:
    result = run_cmd(["snapper", "-c", config, "get-config"])
    for line in result.stdout.splitlines():
        sanitized_line = line.replace("│", "|")
        key, sep, value = sanitized_line.partition("|")
        if sep and key.strip() == "SUBVOLUME":
            target_mnt = value.strip()
            if target_mnt:
                return target_mnt
            break
    fail(f"[!] Fatal: Could not determine SUBVOLUME for snapper config '{config}'.")


def validate_snapshot_id(snap_id: str) -> str:
    snap_id = snap_id.strip()
    if not snap_id.isdigit():
        fail(f"[!] Fatal: Invalid snapshot ID: {snap_id!r}")
    return snap_id


@contextmanager
def mount_top_level(device: str) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(
        prefix="btrfs_top_level_mgmt_",
        dir="/mnt",
        ignore_cleanup_errors=True,
    ) as tmpdir:
        mnt_point = Path(tmpdir)
        print(f"\033[1;38;5;81m[*] Mounting top-level tree (subvolid=5) for {device}...\033[0m", file=sys.stderr)
        run_cmd(["mount", "-o", "subvolid=5", device, str(mnt_point)])

        active_exception: BaseException | None = None
        try:
            yield mnt_point
        except BaseException as exc:
            active_exception = exc
            raise
        finally:
            print("\033[1;38;5;81m[*] Unmounting top-level tree...\033[0m", file=sys.stderr)
            result = run_cmd(["umount", str(mnt_point)], check=False)
            if result.returncode != 0:
                message = error_text(result)
                if active_exception is None:
                    fail(f"[!] Command failed: umount {mnt_point}\n{message}", result.returncode)
                print(f"\033[1;38;5;220m[!] Warning: Failed to unmount top-level tree {mnt_point}: {message}\033[0m", file=sys.stderr)


@dataclass(slots=True)
class RestoreSpec:
    config: str
    snap_id: str
    target_mnt: str
    device: str
    active_subvol: str
    snapshots_subvol: str


@dataclass(slots=True)
class PreparedRestore:
    spec: RestoreSpec
    source_snapshot: Path
    target_path: Path
    temp_delete_path: Path
    staging_path: Path
    staging_created: bool = False
    active_moved: bool = False
    activated: bool = False


def resolve_restore_spec(config: str, snap_id: str) -> RestoreSpec:
    snap_id = validate_snapshot_id(snap_id)
    target_mnt = get_target_mount_from_snapper_config(config)
    snapshots_mnt = "/.snapshots" if target_mnt == "/" else f"{target_mnt}/.snapshots"
    device = get_btrfs_device(target_mnt)
    
    active_subvol = get_active_subvol(target_mnt)
    snapshots_subvol = get_active_subvol(snapshots_mnt)

    if not active_subvol:
        fail(f"[!] Fatal: Empty active subvolume path is not supported for {target_mnt}.")
    if not snapshots_subvol:
        fail(f"[!] Fatal: Empty snapshots subvolume path is not supported for {snapshots_mnt}.")

    return RestoreSpec(
        config=config,
        snap_id=snap_id,
        target_mnt=target_mnt,
        device=device,
        active_subvol=active_subvol,
        snapshots_subvol=snapshots_subvol,
    )


def prepare_restore(spec: RestoreSpec, top_mnt: Path, timestamp: str) -> PreparedRestore:
    target_path = top_mnt / spec.active_subvol
    source_snapshot = top_mnt / spec.snapshots_subvol / spec.snap_id / "snapshot"
    temp_delete_path = target_path.with_name(f"{target_path.name}_to_delete_{timestamp}")
    staging_path = target_path.with_name(f"{target_path.name}_restore_{spec.snap_id}_{timestamp}")

    return PreparedRestore(
        spec=spec,
        source_snapshot=source_snapshot,
        target_path=target_path,
        temp_delete_path=temp_delete_path,
        staging_path=staging_path,
    )


def ensure_no_nested_subvolumes(plan: PreparedRestore) -> None:
    result = run_cmd(["btrfs", "subvolume", "list", "-o", str(plan.target_path)], check=False)
    if result.returncode != 0:
        fail(
            f"[!] Fatal: Failed to inspect nested subvolumes inside "
            f"'{plan.spec.active_subvol}' for config '{plan.spec.config}'.\n"
            f"{error_text(result)}"
        )

    nested_output = result.stdout.strip()
    if nested_output:
        fail(
            f"\n[!] CRITICAL HALT: Nested subvolumes detected physically inside "
            f"'{plan.spec.active_subvol}' for config '{plan.spec.config}'!\n\n"
            f"Offending subvolumes:\n{nested_output}\n\n"
            f"[!] An atomic rollback would trap these inside the subvolume slated for deletion.\n"
            f"[!] Please check what these are. You may need to flatten your Btrfs topology "
            f"(e.g., move Docker to a separate top-level subvolume)."
        )


def rollback_prepared_restores(plans: list[PreparedRestore], original_exc: Exception) -> None:
    rollback_errors: list[str] = []

    for plan in reversed(plans):
        if plan.activated and plan.target_path.exists() and not plan.staging_path.exists():
            try:
                plan.target_path.rename(plan.staging_path)
            except OSError as exc:
                rollback_errors.append(
                    f"{plan.spec.config}: failed to move restored subvolume out of the way: {exc}"
                )

    for plan in reversed(plans):
        if plan.active_moved and plan.temp_delete_path.exists() and not plan.target_path.exists():
            try:
                plan.temp_delete_path.rename(plan.target_path)
            except OSError as exc:
                rollback_errors.append(
                    f"{plan.spec.config}: failed to restore original active subvolume: {exc}"
                )

    for plan in reversed(plans):
        if plan.staging_path.exists():
            result = run_cmd(["btrfs", "subvolume", "delete", str(plan.staging_path)], check=False)
            if result.returncode != 0:
                rollback_errors.append(
                    f"{plan.spec.config}: failed to delete staging subvolume "
                    f"'{plan.staging_path.name}': {error_text(result)}"
                )

    if rollback_errors:
        joined = "\n".join(f"- {item}" for item in rollback_errors)
        fail(
            "[!] Fatal: Restore failed and rollback was incomplete.\n"
            f"{original_exc}\n"
            f"{joined}"
        )

    fail(f"[!] Fatal: Restore failed. Rolled back successfully.\n{original_exc}")


def apply_prepared_restores(plans: list[PreparedRestore]) -> None:
    seen_targets: set[str] = set()

    for plan in plans:
        target_key = str(plan.target_path)
        if target_key in seen_targets:
            fail(f"[!] Fatal: Multiple restore targets resolve to the same path: {target_key}")
        seen_targets.add(target_key)

        if not plan.source_snapshot.is_dir():
            fail(f"[!] Fatal: Snapshot ID {plan.spec.snap_id} does not exist at {plan.source_snapshot}")
        if not plan.target_path.is_dir():
            fail(
                f"[!] Fatal: Active subvolume path does not exist for config "
                f"'{plan.spec.config}': {plan.target_path}"
            )
        if plan.temp_delete_path.exists():
            fail(
                f"[!] Fatal: Deletion path already exists for config "
                f"'{plan.spec.config}': {plan.temp_delete_path}"
            )
        if plan.staging_path.exists():
            fail(
                f"[!] Fatal: Staging path already exists for config "
                f"'{plan.spec.config}': {plan.staging_path}"
            )

        ensure_no_nested_subvolumes(plan)

    try:
        for plan in plans:
            print(
                f"\033[1;38;5;81m[*] Creating staged restore subvolume for '{plan.spec.config}': "
                f"{plan.staging_path.name}...\033[0m"
            )
            run_cmd_raise(
                ["btrfs", "subvolume", "snapshot", str(plan.source_snapshot), str(plan.staging_path)]
            )
            plan.staging_created = True

        for plan in plans:
            print(
                f"\033[1;38;5;81m[*] Unlinking current active subvolume for '{plan.spec.config}'...\033[0m"
            )
            plan.target_path.rename(plan.temp_delete_path)
            plan.active_moved = True

        for plan in plans:
            print(
                f"\033[1;38;5;81m[*] Activating restored snapshot for '{plan.spec.config}' as "
                f"{plan.target_path.name}...\033[0m"
            )
            plan.staging_path.rename(plan.target_path)
            plan.activated = True
            
        # Clean-up Phase: Zero Backup Retention Rule
        for plan in plans:
            print(
                f"\033[1;38;5;81m[*] Permanently deleting previous system state for '{plan.spec.config}'...\033[0m"
            )
            deleted = False
            
            # 1. Immediate Retry Loop for transient Btrfs locking
            for attempt in range(3):
                del_res = run_cmd(["btrfs", "subvolume", "delete", str(plan.temp_delete_path)], check=False)
                if del_res.returncode == 0:
                    deleted = True
                    break
                time.sleep(1)

            # 2. Aggressive Background Cleanup Fallback
            if not deleted:
                print(f"\033[1;38;5;220m[!] Warning: Immediate deletion failed. Scheduling aggressive background cleanup on next boot...\033[0m", file=sys.stderr)
                try:
                    # Dynamically capture UUID to perfectly handle LUKS and LVM mappings upon next boot
                    uuid_res = run_cmd(["findmnt", "-n", "-e", "-o", "UUID", "-M", plan.spec.target_mnt], check=False)
                    uuid = uuid_res.stdout.strip()
                    
                    # Mirroring defensive bash logic: fallback to blkid if findmnt returns blank/dash
                    if not uuid or uuid == "-":
                        device_res = run_cmd(["findmnt", "-n", "-v", "-e", "-o", "SOURCE", "-M", plan.spec.target_mnt], check=False)
                        device = device_res.stdout.strip()
                        if device.startswith("/dev/"):
                            blkid_res = run_cmd(["blkid", "-s", "UUID", "-o", "value", device], check=False)
                            uuid = blkid_res.stdout.strip()

                    if not uuid:
                        print(f"\033[1;38;5;196m[!] Error: Could not determine UUID for {plan.spec.target_mnt}. Manual deletion of '{plan.temp_delete_path.name}' required.\033[0m", file=sys.stderr)
                        continue

                    subvol_name = plan.temp_delete_path.name
                    service_name = f"dusky-cleanup-{subvol_name}.service"
                    service_path = Path("/etc/systemd/system") / service_name
                    
                    # Generate a self-destructing, one-shot systemd service executing right after decryption/mount targets
                    service_content = f"""[Unit]
Description=Dusky Btrfs Cleanup ({subvol_name})
After=local-fs.target

[Service]
Type=oneshot
ExecStart=/usr/bin/bash -c "/usr/bin/mkdir -p /run/dusky_mnt && /usr/bin/mount -t btrfs -o subvolid=5 UUID={uuid} /run/dusky_mnt && {{ /usr/bin/btrfs subvolume delete '/run/dusky_mnt/{subvol_name}'; /usr/bin/umount /run/dusky_mnt; }}"
ExecStartPost=/usr/bin/systemctl disable {service_name}
ExecStartPost=/usr/bin/rm -f /etc/systemd/system/{service_name}

[Install]
WantedBy=multi-user.target
"""
                    service_path.write_text(service_content)
                    run_cmd(["systemctl", "daemon-reload"])
                    run_cmd(["systemctl", "enable", service_name])
                    
                    print(f"\033[1;38;5;114m[+] Scheduled one-shot systemd service '{service_name}' to eradicate subvolume on next boot.\033[0m")
                except Exception as e:
                    print(f"\033[1;38;5;196m[!] Failed to schedule boot cleanup: {e}\n[!] Manual deletion of '{plan.temp_delete_path.name}' required.\033[0m", file=sys.stderr)

    except (OSError, RuntimeError) as exc:
        rollback_prepared_restores(plans, exc)


def is_mountpoint(path: str) -> bool:
    result = run_cmd(["mountpoint", "-q", "--", path], check=False)
    return result.returncode == 0


def activate_nonroot_restore(target_mnt: str) -> None:
    """
    Attempts to live-remount a non-root subvolume (like /home).
    Gracefully handles busy states by returning success and notifying the user to reboot.
    """
    if not is_mountpoint(target_mnt):
        print(
            f"\033[1;38;5;81m[*] {target_mnt} is not currently mounted as its own mountpoint. "
            f"Restored subvolume will be used on the next mount.\033[0m"
        )
        return

    print(f"\033[1;38;5;81m[*] Attempting to live-remount {target_mnt} to activate restored snapshot...\033[0m")

    umount_result = run_cmd(["umount", target_mnt], check=False)
    if umount_result.returncode != 0:
        # Target is busy (expected for live mounts like /home where terminal/DE is running)
        print(
            f"\n\033[1;38;5;220m[!] Notice: {target_mnt} is currently in use (target is busy).\n"
            f"[!] The restore was successful on disk, but the live filesystem cannot be swapped.\n"
            f"[\033[1;38;5;196m!\033[1;38;5;220m] WARNING: Any changes made to {target_mnt} right now will be lost upon reboot.\n"
            f"[!] Please REBOOT IMMEDIATELY to activate the restored snapshot.\033[0m"
        )
        return

    # If unmount succeeds, we MUST remount it to avoid breaking the active session
    mount_result = run_cmd(["mount", target_mnt], check=False)
    if mount_result.returncode != 0:
        # This is an actual critical failure: it's no longer mounted at all.
        fail(
            f"[!] CRITICAL: Restore completed on disk, but remount of {target_mnt} failed!\n"
            f"{error_text(mount_result)}\n"
            f"[!] Your {target_mnt} directory is currently unmounted. Please resolve manually before rebooting."
        )

    print(f"\033[1;38;5;114m[+] {target_mnt} successfully remounted live.\033[0m")


def first_present(mapping: dict[str, object], *keys: str) -> object | None:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def normalize_json_key(value: str) -> str:
    raw = value.strip()
    if raw == "#":
        return "number"

    normalized = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    aliases = {
        "num": "number",
        "number": "number",
        "id": "id",
        "snapshot_id": "id",
        "type": "type",
        "snapshot_type": "snapshot_type",
        "date": "date",
        "timestamp": "timestamp",
        "time": "time",
        "description": "description",
        "desc": "description",
    }
    return aliases.get(normalized, normalized)


def looks_like_snapshot_record(obj: object) -> bool:
    if not isinstance(obj, dict):
        return False

    id_value = first_present(obj, "number", "id", "num", "#")
    aux_value = first_present(obj, "date", "timestamp", "time", "description", "desc", "type", "snapshot_type")
    return id_value is not None and aux_value is not None


def find_snapshot_records(obj: object) -> list[dict[str, object]] | None:
    if isinstance(obj, list):
        if obj and all(isinstance(item, dict) for item in obj) and any(looks_like_snapshot_record(item) for item in obj):
            return list(obj)
        for item in obj:
            found = find_snapshot_records(item)
            if found is not None:
                return found
        return None

    if isinstance(obj, dict):
        for key in ("snapshots", "entries", "data", "list"):
            if key in obj:
                found = find_snapshot_records(obj[key])
                if found is not None:
                    return found
        for value in obj.values():
            found = find_snapshot_records(value)
            if found is not None:
                return found

    return None


def find_tabular_snapshot_records(obj: object) -> list[dict[str, object]] | None:
    if isinstance(obj, dict):
        columns = obj.get("columns")
        rows = obj.get("rows")
        if rows is None:
            rows = obj.get("data")

        if isinstance(columns, list) and isinstance(rows, list):
            column_names: list[str] = []
            for column in columns:
                if isinstance(column, str):
                    column_names.append(normalize_json_key(column))
                elif isinstance(column, dict):
                    label = None
                    for candidate in ("name", "key", "id", "title", "label"):
                        if candidate in column and column[candidate] is not None:
                            label = str(column[candidate])
                            break
                    column_names.append(normalize_json_key("" if label is None else label))
                else:
                    column_names.append("")

            if rows and all(isinstance(row, dict) for row in rows):
                candidate_rows = [dict(row) for row in rows]
                if any(looks_like_snapshot_record(row) for row in candidate_rows):
                    return candidate_rows

            if rows and all(isinstance(row, (list, tuple)) for row in rows):
                records: list[dict[str, object]] = []
                for row in rows:
                    record: dict[str, object] = {}
                    for index, value in enumerate(row):
                        key = column_names[index] if index < len(column_names) and column_names[index] else f"col_{index}"
                        record[key] = value
                    records.append(record)
                if records and any(looks_like_snapshot_record(record) for record in records):
                    return records

        for value in obj.values():
            found = find_tabular_snapshot_records(value)
            if found is not None:
                return found

    elif isinstance(obj, list):
        for item in obj:
            found = find_tabular_snapshot_records(item)
            if found is not None:
                return found

    return None


def extract_snapshot_records(payload: object) -> list[dict[str, object]] | None:
    records = find_snapshot_records(payload)
    if records is not None:
        return records
    return find_tabular_snapshot_records(payload)


def parse_snapshot_datetime(raw_value: object) -> datetime | None:
    if raw_value is None:
        return None

    if isinstance(raw_value, int | float):
        try:
            return datetime.fromtimestamp(raw_value)
        except (OverflowError, OSError, ValueError):
            return None

    raw = str(raw_value).strip()
    if not raw:
        return None

    iso_candidates = [raw]
    if " " in raw:
        iso_candidates.append(raw.replace(" ", "T", 1))

    for candidate in iso_candidates:
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            pass

    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, pattern)
        except ValueError:
            continue

    tokens = raw.split()
    if len(tokens) >= 7 and tokens[-1].isalpha():
        try:
            clean_date = " ".join(tokens[:-1])
            return datetime.strptime(clean_date, "%a %d %b %Y %I:%M:%S %p")
        except ValueError:
            pass

    return None


def format_snapshot_date(raw_value: object) -> str:
    dt = parse_snapshot_datetime(raw_value)
    if dt is not None:
        return dt.strftime("%m/%d/%y %I:%M %p")
    return str(raw_value).strip() if raw_value is not None else ""


def time_ago(dt: datetime) -> str:
    now = datetime.now()
    diff = now - dt
    seconds = int(diff.total_seconds())
    if seconds < 0: return "Just now"
    if seconds < 60: return f"{seconds}s ago"
    if seconds < 3600: return f"{seconds // 60}m ago"
    if seconds < 86400: return f"{seconds // 3600}h ago"
    if seconds < 2592000: return f"{seconds // 86400}d ago"
    return f"{seconds // 2592000}mo ago"


def snapshot_records_to_gui(records: list[dict[str, object]]) -> list[dict[str, str]]:
    gui_data: list[dict[str, str]] = []

    for record in records:
        snap_id_value = first_present(record, "number", "id", "num", "#")
        if snap_id_value is None:
            continue

        snap_id = str(snap_id_value).strip()
        if snap_id == "0" or not snap_id.isdigit():
            continue

        raw_date_value = first_present(record, "date", "timestamp", "time")
        raw_date = "" if raw_date_value is None else str(raw_date_value)
        
        # Enhanced Metadata Extraction for Preview Pane enrichment
        cleanup = str(first_present(record, "cleanup", "cleanup_algorithm") or "")
        userdata = str(first_present(record, "userdata", "user_data") or "")
        user = str(first_present(record, "user", "creator") or "root")
        pre_num = str(first_present(record, "pre_number", "pre_num") or "")
        if pre_num == "0": 
            pre_num = ""

        gui_data.append(
            {
                "id": snap_id,
                "type": str(first_present(record, "type", "snapshot_type") or ""),
                "date": format_snapshot_date(raw_date_value),
                "raw_date": raw_date,
                "description": str(first_present(record, "description", "desc") or ""),
                "cleanup": cleanup,
                "userdata": userdata,
                "user": user,
                "pre_number": pre_num
            }
        )

    return gui_data


def parse_snapper_table(stdout: str) -> list[dict[str, str]]:
    gui_data: list[dict[str, str]] = []

    for line in stdout.splitlines():
        if not line.strip():
            continue

        parts = [part.strip() for part in re.split(r"[|│]", line)]
        if len(parts) < 7:
            continue

        snap_id = parts[0]
        if snap_id == "0" or not snap_id.isdigit():
            continue

        # Column Layout from Snapper Text:
        # # | Type | Pre # | Date | User | Cleanup | Description | Userdata
        snap_type = parts[1]
        pre_num = parts[2] if parts[2] != "-" else ""
        raw_date = parts[3]
        user = parts[4] if len(parts) > 4 else "root"
        cleanup = parts[5] if len(parts) > 5 else ""
        description = parts[6] if len(parts) > 6 else ""
        
        # Safely extract trailing userdata even if description internally contained pipes
        userdata = parts[7] if len(parts) > 7 else ""
        if len(parts) > 8:
            userdata = "|".join(parts[7:]).strip()

        gui_data.append(
            {
                "id": snap_id,
                "type": snap_type,
                "date": format_snapshot_date(raw_date),
                "raw_date": raw_date,
                "description": description,
                "cleanup": cleanup,
                "userdata": userdata,
                "user": user,
                "pre_number": pre_num
            }
        )

    return gui_data


def load_snapshot_list_for_gui_from_text(config: str) -> list[dict[str, str]]:
    result = run_cmd(["snapper", "-c", config, "list", "--disable-used-space"], check=False)
    if result.returncode != 0:
        return []
    return parse_snapper_table(result.stdout)


def load_snapshot_list_for_gui(config: str) -> list[dict[str, str]]:
    result = run_cmd(["snapper", "--jsonout", "-c", config, "list", "--disable-used-space"], check=False)
    if result.returncode != 0:
        return []

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return load_snapshot_list_for_gui_from_text(config)

    records = extract_snapshot_records(payload)
    if records is None:
        return load_snapshot_list_for_gui_from_text(config)

    return snapshot_records_to_gui(records)


def find_coordinated_pair(target_date: str, target_desc: str | None = None) -> tuple[str, str]:
    root_snaps = load_snapshot_list_for_gui("root")
    if not root_snaps:
        raise RuntimeError("[!] Fatal: Failed to query Root snapshots or list is empty.")

    home_snaps = load_snapshot_list_for_gui("home")
    if not home_snaps:
        raise RuntimeError("[!] Fatal: Failed to query Home snapshots or list is empty.")

    # --- Match Root ID ---
    exact_root = [s["id"] for s in root_snaps if s.get("raw_date") == target_date]
    if len(exact_root) == 1:
        root_id = exact_root[0]
    elif not exact_root:
        raise RuntimeError(f"[!] Fatal: Could not find Root snapshot for exact date: {target_date}")
    else:
        raise RuntimeError(f"[!] Fatal: Multiple Root snapshots matched exact date: {target_date}")

    # --- Match Home ID (Attempt 1: Exact) ---
    exact_home = [s["id"] for s in home_snaps if s.get("raw_date") == target_date]
    if len(exact_home) == 1:
        return root_id, exact_home[0]
    if len(exact_home) > 1:
        raise RuntimeError(f"[!] Fatal: Multiple Home snapshots matched exact date: {target_date}")

    # --- Match Home ID (Attempt 2: Fuzzy Minute Match) ---
    def minute_prefix(val: str) -> str | None:
        match = re.search(r"^(.*\d{2}:\d{2})", val)
        return match.group(1) if match else None

    if target_desc:
        t_min = minute_prefix(target_date)
        if t_min:
            fuzzy = [
                s["id"] for s in home_snaps 
                if s.get("description") == target_desc 
                and minute_prefix(s.get("raw_date", "")) == t_min
            ]
            if len(fuzzy) == 1:
                return root_id, fuzzy[0]
            if len(fuzzy) > 1:
                raise RuntimeError("[!] Fatal: Multiple Home snapshots matched fuzzy minute+description.")

    # --- Match Home ID (Attempt 3: Strict 120s Safety Fallback) ---
    target_dt = parse_snapshot_datetime(target_date)
    if not target_dt:
        raise RuntimeError("[!] Fatal: Date parsing failed for target date. Cannot perform 120s safety fallback.")

    best_diff = float('inf')
    best_id = None

    for s in home_snaps:
        s_dt = parse_snapshot_datetime(s.get("raw_date", ""))
        if s_dt:
            diff = abs((s_dt - target_dt).total_seconds())
            if diff < best_diff:
                best_diff = diff
                best_id = s["id"]

    if best_id is not None and best_diff <= 120:
        return root_id, best_id
    
    if best_id is not None:
        raise RuntimeError(f"[!] Fatal: Closest Home snapshot (ID {best_id}) is {best_diff:.1f}s away. Exceeds strict 120s safety threshold.")

    raise RuntimeError("[!] Fatal: No safe synchronized match found. Aborting.")


def confirm_prompt(prompt: str) -> bool:
    while True:
        try:
            choice = input(f"\n\033[1;38;5;220m{prompt} [y/N]: \033[0m").strip().lower()
        except KeyboardInterrupt:
            print("\nAborted.")
            sys.exit(130)
        
        if choice in ('y', 'yes'):
            return True
        if choice in ('', 'n', 'no'):
            return False
        print("Please answer y or n.")


# -----------------------------------------------------------------------------
# CORE COMMAND HANDLERS
# -----------------------------------------------------------------------------

def handle_list(config: str, as_json: bool) -> None:
    if not as_json:
        sys.exit(run_passthrough(["snapper", "-c", config, "list"]))
    print(json.dumps(load_snapshot_list_for_gui(config), ensure_ascii=False))


def handle_create(config: str, description: str) -> None:
    print(f"\033[1;38;5;81m[*] Creating snapshot for '{config}': {description}\033[0m")
    run_cmd(["snapper", "-c", config, "create", "-d", description])
    print(f"\033[1;38;5;114m[+] Snapshot created successfully for '{config}'.\033[0m")


def handle_create_pair(config1: str, config2: str, description: str) -> None:
    print(f"\033[1;38;5;81m[*] Creating coordinated snapshots for '{config1}' and '{config2}': {description}\033[0m")
    run_cmd(["snapper", "-c", config1, "create", "-d", description])
    run_cmd(["snapper", "-c", config2, "create", "-d", description])
    print("\033[1;38;5;114m[+] Coordinated snapshots created successfully.\033[0m")


def handle_restore(config: str, snap_id: str, no_remount: bool = False) -> None:
    spec = resolve_restore_spec(config, snap_id)

    with mount_top_level(spec.device) as top_mnt:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        plan = prepare_restore(spec, top_mnt, timestamp)
        apply_prepared_restores([plan])

    print(f"\n\033[1;38;5;114m[+] Restoration of '{config}' complete.\033[0m")
    if spec.target_mnt == "/":
        print("\033[1;38;5;196m[!] ROOT FILESYSTEM RESTORED. You MUST reboot immediately for changes to take effect.\033[0m")
        return

    if no_remount:
        print(f"\033[1;38;5;220m[!] {spec.target_mnt} was restored on disk without live remount.\n[!] Reboot or manually remount to activate.\033[0m")
        return

    activate_nonroot_restore(spec.target_mnt)


def handle_restore_pair(config1: str, snap_id1: str, config2: str, snap_id2: str) -> None:
    if config1 == config2:
        fail("[!] Fatal: Coordinated restore requires two distinct snapper configs.")

    spec1 = resolve_restore_spec(config1, snap_id1)
    spec2 = resolve_restore_spec(config2, snap_id2)

    devices = {spec1.device, spec2.device}
    if len(devices) != 1:
        fail("[!] Fatal: Coordinated restore requires both configs to live on the same Btrfs filesystem.")

    if spec1.active_subvol == spec2.active_subvol:
        fail("[!] Fatal: Coordinated restore configs resolve to the same active subvolume path.")

    with mount_top_level(spec1.device) as top_mnt:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        plans = [
            prepare_restore(spec1, top_mnt, timestamp),
            prepare_restore(spec2, top_mnt, timestamp),
        ]
        apply_prepared_restores(plans)

    print("\n\033[1;38;5;114m[+] Coordinated restoration complete.\033[0m")
    if spec1.target_mnt == "/" or spec2.target_mnt == "/":
        print("\033[1;38;5;196m[!] ROOT FILESYSTEM MODIFIED. You MUST reboot immediately for changes to take effect.\033[0m")
    else:
        print("\033[1;38;5;220m[!] Restored subvolumes were staged on disk. Reboot to activate.\033[0m")


def handle_delete(config: str, snap_id: str) -> None:
    snap_id = validate_snapshot_id(snap_id)
    if snap_id == "0":
        fail(f"[!] Fatal: Cannot delete snapshot ID 0 (the active system state) for config '{config}'.")
    
    print(f"\033[1;38;5;81m[*] Deleting snapshot ID {snap_id} for '{config}'...\033[0m")
    run_cmd(["snapper", "-c", config, "delete", snap_id])
    print(f"\033[1;38;5;114m[+] Snapshot ID {snap_id} deleted successfully.\033[0m")


def handle_delete_pair(config1: str, snap_id1: str, config2: str, snap_id2: str) -> None:
    if config1 == config2:
        fail("[!] Fatal: Coordinated deletion requires two distinct snapper configs.")
        
    handle_delete(config1, snap_id1)
    handle_delete(config2, snap_id2)
    print("\n\033[1;38;5;114m[+] Coordinated deletion complete.\033[0m")


# -----------------------------------------------------------------------------
# FZF TUI INTEGRATION
# -----------------------------------------------------------------------------

def handle_tui_preview(view: str, line: str, show_diff: bool = False) -> None:
    """Invoked asynchronously by FZF to generate the dynamic side-pane preview."""
    try:
        # Separate the visible fzf text from the hidden metadata payload packed via JSON
        line_parts = line.split('\x1f')
        visible_line = line_parts[0]
        extra_data = {}
        if len(line_parts) > 1:
            try:
                extra_data = json.loads(line_parts[1])
            except ValueError:
                pass
        
        # Strip all ANSI escape sequences to process raw FZF line natively
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        clean_line = ansi_escape.sub('', visible_line)
        parts = [p.strip() for p in clean_line.split("│")]
        
        if not parts or not parts[0].isdigit():
            print("\n\033[3;38;5;246m  No snapshot selected or invalid layout.\033[0m")
            return
            
        snap_id = parts[0]
        snap_type = parts[1] if len(parts) > 1 else "Unknown"
        snap_date = parts[3] if len(parts) > 3 else "Unknown"
        snap_desc = parts[4] if len(parts) > 4 else "No Description"
        
        # Robustly unpack the highly specific hidden data provided by the engine
        snap_user = extra_data.get("user", "root")
        snap_cleanup = extra_data.get("cleanup", "")
        snap_userdata = extra_data.get("userdata", "")
        snap_pre_num = extra_data.get("pre_number", "")
        snap_age = extra_data.get("age", "")
        
        # 1. Cleanly Aligned Shortcuts Panel (Math-Calculated Padding exactly 44 chars wide ensuring right-side borders lock tight)
        print("\033[1;38;5;220m╭─ 󰏖 KEYBOARD SHORTCUTS " + "─"*19 + "╮\033[0m")
        print("\033[1;38;5;220m│\033[0m \033[1;38;5;114m[ENTER]\033[0m   \033[38;5;253m󰁯 Restore Selected\033[0m" + " "*13 + "\033[1;38;5;220m│\033[0m")
        print("\033[1;38;5;220m│\033[0m \033[1;38;5;196m[DEL]\033[0m     \033[38;5;253m󰆴 Delete Selected\033[0m" + " "*14 + "\033[1;38;5;220m│\033[0m")
        print("\033[1;38;5;220m│\033[0m \033[1;38;5;81m[CTRL-S]\033[0m  \033[38;5;253m󰎈 Create New Snapshot\033[0m" + " "*10 + "\033[1;38;5;220m│\033[0m")
        print("\033[1;38;5;220m│\033[0m \033[1;38;5;213m[TAB]\033[0m     \033[38;5;253m󰓡 Switch View (Root/Home)\033[0m" + " "*6 + "\033[1;38;5;220m│\033[0m")
        print("\033[1;38;5;220m│\033[0m \033[1;38;5;246m[CTRL-A]\033[0m  \033[38;5;253m󰒉 Select All\033[0m" + " "*19 + "\033[1;38;5;220m│\033[0m")
        print("\033[1;38;5;220m│\033[0m \033[1;38;5;246m[CTRL-X]\033[0m  \033[38;5;253m󰒓 Deselect All\033[0m" + " "*17 + "\033[1;38;5;220m│\033[0m")
        print("\033[1;38;5;220m╰" + "─"*42 + "╯\033[0m\n")

        # 2. Expanded Snapshot Meta Data leveraging hidden JSON payloads matching 44-char width design
        print(f"\033[1;38;5;81m󰆑 SNAPSHOT DETAILS\033[0m")
        print(f"\033[38;5;238m" + "─" * 44 + "\033[0m")
        print(f" \033[1;38;5;246mConfig \033[0m │ \033[1;38;5;253m{view.upper()}\033[0m")
        print(f" \033[1;38;5;246mID     \033[0m │ \033[1;38;5;39m{snap_id}\033[0m")
        print(f" \033[1;38;5;246mType   \033[0m │ \033[38;5;213m{snap_type}\033[0m")
        if snap_type.lower() == "post" and snap_pre_num:
            print(f" \033[1;38;5;246mPre-ID \033[0m │ \033[38;5;216m{snap_pre_num}\033[0m")
        print(f" \033[1;38;5;246mDate   \033[0m │ \033[38;5;220m{snap_date}\033[0m")
        if snap_age:
            print(f" \033[1;38;5;246mAge    \033[0m │ \033[38;5;114m{snap_age}\033[0m")
        print(f" \033[1;38;5;246mUser   \033[0m │ \033[38;5;114m{snap_user}\033[0m")
        if snap_cleanup and snap_cleanup.lower() != "none":
            print(f" \033[1;38;5;246mCleanup\033[0m │ \033[38;5;216m{snap_cleanup}\033[0m")
        if snap_userdata and snap_userdata.lower() != "none":
            print(f" \033[1;38;5;246mData   \033[0m │ \033[38;5;253m{snap_userdata}\033[0m")
        print(f" \033[1;38;5;246mDesc   \033[0m │ \033[38;5;253m{snap_desc}\033[0m\n")

        # 3. Dynamic Diff generation via snapper status (Snapshot -> Current)
        # Only runs when explicitly requested via Ctrl+V for instant UI responsiveness
        if show_diff:
            print(f"\033[1;38;5;114m󰏫 FILES CHANGED IF RESTORED\033[0m \033[3;38;5;246m(vs Current System)\033[0m")
            print(f"\033[38;5;238m" + "─" * 44 + "\033[0m")

            def run_diff(config: str, s_id: str):
                print(f"\033[1;38;5;203m▶ System Profile: {config}\033[0m")
                try:
                    # Comparing <id> against 0 shows what happened *since* the snapshot
                    # i.e., What will happen to current files if we revert.
                    result = subprocess.run(["snapper", "-c", config, "status", f"{s_id}..0"], capture_output=True, text=True)
                    if result.returncode != 0:
                        print(f"  \033[38;5;196mError extracting diff: {result.stderr.strip()}\033[0m")
                        return

                    lines = result.stdout.splitlines()
                    if not lines:
                        print("  \033[3;38;5;246mNo file changes detected since snapshot.\033[0m")
                        return

                    max_lines = 100
                    for i, l in enumerate(lines):
                        if i >= max_lines:
                            print(f"  \033[3;38;5;246m... and {len(lines) - max_lines} more files ...\033[0m")
                            break

                        if not l.strip():
                            continue

                        status = l[0]
                        filepath = l[6:].strip() if len(l) > 6 else l[1:].strip()

                        # Logic Mapping (What the reversion does to the current system state)
                        if status == '+': # File was added *after* snapshot -> Reverting will Delete it
                            print(f"  \033[1;38;5;196m[-]\033[0m \033[38;5;246m{filepath}\033[0m")
                        elif status == '-': # File was removed *after* snapshot -> Reverting will Restore it
                            print(f"  \033[1;38;5;114m[+]\033[0m \033[38;5;253m{filepath}\033[0m")
                        elif status == 'c': # File was modified -> Reverting will modify it back
                            print(f"  \033[1;38;5;220m[~]\033[0m \033[38;5;253m{filepath}\033[0m")
                        else:
                            print(f"  \033[38;5;246m{l}\033[0m")
                except Exception as e:
                    print(f"  \033[38;5;196mExecution Failed: {e}\033[0m")

            if view in ("root", "home"):
                run_diff(view, snap_id)
            elif view == "coordinated":
                run_diff("root", snap_id)
                print()
                try:
                    r_id, h_id = find_coordinated_pair(snap_date, snap_desc)
                    run_diff("home", h_id)
                except RuntimeError:
                    print(f"\033[1;38;5;203m▶ System Profile: home\033[0m")
                    print("  \033[3;38;5;196mFailed to locate paired snapshot.\033[0m")
        else:
            print(f"\033[1;38;5;246m[!] File changes hidden for performance.\033[0m")
            print(f"\033[1;38;5;246mPress \033[1;38;5;220m<Ctrl+V>\033[1;38;5;246m to generate file change list.\033[0m")
            print(f"\033[1;38;5;246mPress \033[1;38;5;220m<Ctrl+B>\033[1;38;5;246m to hide and restore fast scrolling.\033[0m")

    except Exception as e:
        print(f"\033[1;38;5;196mError generating TUI Preview Pane:\n{e}\033[0m")


def launch_tui() -> None:
    if not shutil.which("fzf"):
        fail("[!] Fatal: 'fzf' is required for the interactive menu. Install it using: pacman -S fzf")

    # Reordered tabs to enforce exact user specification: Home -> Root -> Root+Home
    views = ["home", "root", "coordinated"]
    view_idx = 0

    # Catppuccin Mocha vivid theme styling extracted from pkg reference
    fzf_colors = (
        "bg+:#1e1e2e,bg:#11111b,spinner:#f5e0dc,"
        "fg:#cdd6f4,fg+:#cdd6f4,header:#89b4fa,info:#cba6f7,"
        "pointer:#f5e0dc,marker:#a6e3a1,prompt:#cba6f7,"
        "hl:#f38ba8,hl+:#f38ba8,border:#585b70,label:#a6e3a1"
    )

    executable = shlex.quote(sys.executable)
    script_path = shlex.quote(os.path.abspath(sys.argv[0]))

    while True:
        current_view = views[view_idx]
        
        # Fetch instantaneous Global Btrfs Storage usage natively
        gb = 1024**3
        total, used, free = shutil.disk_usage("/")
        
        # --- TOP LEVEL STORAGE HEADER ---
        # This will be injected *above* the prompt line via `--header-first`
        storage_hdr = f" \033[1;38;5;81m󰋊 BTRFS STORAGE:\033[0m \033[38;5;253m{total/gb:.1f} GB Total\033[0m \033[38;5;238m|\033[0m \033[38;5;203m{used/gb:.1f} GB Used\033[0m \033[38;5;238m|\033[0m \033[38;5;114m{free/gb:.1f} GB Free\033[0m "
        
        # --- DYNAMIC INTERACTIVE TABS GENERATION ---
        # Reordered tab definitions. Layout structure permits flawless FZF mouse coordination
        tab_defs = [
            ("home", "󰋜 HOME ONLY", "114"),
            ("root", "󰒋 ROOT ONLY", "39"),
            ("coordinated", "󰑐 ROOT+HOME", "213")
        ]

        tab_strs = []
        for v_id, label, color in tab_defs:
            if v_id == current_view:
                # Active tab styling: Deep matching background block
                tab_strs.append(f"\033[1;38;5;232;48;5;{color}m {label} \033[0m")
            else:
                # Inactive tab styling: Transparent/dimmer
                tab_strs.append(f"\033[38;5;246m {label} \033[0m")

        # Constructed interactively below the prompt via `--header-lines`
        mode_hdr = "  " + "  ".join(tab_strs)
        
        # --- SEPARATOR AND PERFECTLY ALIGNED TABLE HEADERS ---
        c_sep = "\033[38;5;238m│\033[0m"
        
        # We deliberately over-render the horizontal line to 500 characters. 
        # FZF naturally clips this out cleanly at the list pane boundary (with --no-hscroll active).
        # This acts as a bulletproof workaround for Python's standard TTY limits inside piped processes.
        hr = "\033[38;5;238m" + "─" * 500 + "\033[0m"
        
        # Employs identical Python format layout bounds (`:>4`, `:<7`, etc) to perfectly align with data rows 
        hdr_id = f"\033[1;38;5;242m{'ID':>4}\033[0m"
        hdr_type = f"\033[1;38;5;242m{'TYPE':<7}\033[0m"
        hdr_age = f"\033[1;38;5;242m{'AGE':<10}\033[0m"
        hdr_date = f"\033[1;38;5;242m{'DATE':<18}\033[0m"
        hdr_desc = f"\033[1;38;5;242mDESCRIPTION\033[0m"
        
        # The gutter offset will be automatically preserved by FZF because it's passed as a `--header-line`
        table_hdr = f"{hdr_id} {c_sep} {hdr_type} {c_sep} {hdr_age} {c_sep} {hdr_date} {c_sep} {hdr_desc}"
        
        # --- CONSTRUCTING THE INPUT STREAM ---
        lines_for_fzf = []
        
        # 1. Provide Sticky UI Headers to be fixed right underneath the fzf prompt:
        lines_for_fzf.append(mode_hdr)
        lines_for_fzf.append(hr)
        lines_for_fzf.append(table_hdr)
        
        # 2. Append Snapshot Data:
        config_to_query = "root" if current_view in ("coordinated", "root") else "home"
        snaps = load_snapshot_list_for_gui(config_to_query)
        snap_map = {}
        
        if snaps:
            snap_map = {s["id"]: s for s in snaps}
            snaps_sorted = sorted(snaps, key=lambda x: int(x["id"]), reverse=True)

            for s in snaps_sorted:
                dt = parse_snapshot_datetime(s["raw_date"])
                age_str = time_ago(dt) if dt else "Unknown"
                
                # Symmetrical constraints ensure matching with `table_hdr`
                id_str = f"\033[1;38;5;39m{s['id']:>4}\033[0m"         
                type_str = f"\033[38;5;213m{s['type']:<7}\033[0m"       
                age_colored = f"\033[38;5;114m{age_str:<10}\033[0m"    
                date_str = f"\033[38;5;220m{s['date']:<18}\033[0m"     
                desc_str = f"\033[38;5;253m{s['description']}\033[0m"  
                
                visible_line = f"{id_str} {c_sep} {type_str} {c_sep} {age_colored} {c_sep} {date_str} {c_sep} {desc_str}"
                
                # Bundle extended meta-data properties extracted from snapper into an invisible FZF payload
                # This guarantees 0-latency previews for FZF by injecting the raw metadata entirely off-screen
                extra_data = {
                    "user": s.get("user", "root"),
                    "cleanup": s.get("cleanup", ""),
                    "userdata": s.get("userdata", ""),
                    "pre_number": s.get("pre_number", ""),
                    "age": age_str
                }
                
                # Separated by hex control char '\x1f' ensuring zero collisions with UI aesthetics
                lines_for_fzf.append(f"{visible_line}\x1f{json.dumps(extra_data)}")
        else:
            lines_for_fzf.append(f"\033[1;38;5;196m No snapshots found for '{config_to_query}' configuration.\033[0m")

        # Utilizing strict fzf 0.73.1 syntax and layout logic for async previewing
        preview_cmd = f"{executable} {script_path} --tui-preview {current_view} {{}}"
        preview_diff_cmd = f"{executable} {script_path} --tui-preview {current_view} --show-diff {{}}"

        # Layout Engine Upgrades:
        # 1. `--border-label` interrupts the frame exactly with "Dusky Snapshots"
        # 2. `--prompt` shifted to strictly say ":: Snapshots ❯"
        # 3. `--header` isolates the Storage metric and `--header-first` pins it exactly above the prompt
        # 4. `--header-lines=3` pins the Tabs and Table aligned exactly underneath the prompt.
        # 5. `--delimiter=\x1f` and `--with-nth=1` silently truncate JSON payloads from FZF display!
        fzf_cmd = [
            "fzf",
            "--multi",
            "--ansi",
            "--reverse",
            "--delimiter=\\x1f",
            "--with-nth=1",
            "--header", storage_hdr,
            "--header-first",
            "--header-lines=3",
            "--border=rounded",
            "--border-label", " Dusky Snapshots ",
            "--prompt= :: Snapshots ❯ ",
            f"--color={fzf_colors}",
            "--pointer=▌", 
            "--marker=▶",
            "--no-hscroll",
            "--ellipsis=",
            "--expect=enter,ctrl-d,delete,tab,ctrl-s,alt-s",
            f"--bind=click-header:become(echo click-header; echo $FZF_CLICK_HEADER_LINE; echo $FZF_CLICK_HEADER_COLUMN),ctrl-a:select-all,ctrl-x:deselect-all,ctrl-space:toggle,shift-down:toggle+down,shift-up:toggle+up,ctrl-p:toggle-preview,ctrl-v:change-preview({preview_diff_cmd})+change-prompt( :: Diff Mode ON (Slower) ❯ ),ctrl-b:change-preview({preview_cmd})+change-prompt( :: Snapshots ❯ )",
            "--info=hidden",
            "--preview", preview_cmd,
            "--preview-window", "right,45%,border-left,wrap"
        ]

        try:
            process = subprocess.Popen(fzf_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8")
            stdout, _ = process.communicate(input="\n".join(lines_for_fzf))
        except Exception as exc:
            fail(f"[!] FZF Execution failed: {exc}")

        if not stdout.strip():
            break # User pressed ESC or aborted

        output_lines = stdout.strip().split("\n")
        key_pressed = output_lines[0]
        
        # --- Handle Active Tab Toggle (Via Mouse / click-header) ---
        if key_pressed == "click-header":
            line = int(output_lines[1]) if len(output_lines) > 1 and output_lines[1].isdigit() else 0
            col = int(output_lines[2]) if len(output_lines) > 2 and output_lines[2].isdigit() else 0
            
            # Since mode_hdr is passed within --header-lines alongside the normal header, it safely triggers within lines 1 & 2.
            # Bounds account exactly for the shifted FZF gutter offset (+2 chars).
            if line in (1, 2):
                if col <= 18:
                    view_idx = 0      # Home
                elif col <= 33:
                    view_idx = 1      # Root
                elif col <= 48:
                    view_idx = 2      # Root+Home
            continue
        
        # --- Handle Active Tab Toggle (Via Keyboard / TAB key) ---
        if key_pressed == "tab":
            view_idx = (view_idx + 1) % len(views)
            continue
            
        # Snapshot Creation Logic
        if key_pressed in ("ctrl-s", "alt-s"):
            print(f"\n\033[1;38;5;81m[*] Action: CREATE NEW SNAPSHOT ({current_view.upper()})\033[0m")
            try:
                desc = input("\033[1;38;5;220m[*] Enter description for the new snapshot:\033[0m ").strip()
                if desc:
                    if current_view == "coordinated":
                        handle_create_pair("root", "home", desc)
                    elif current_view == "root":
                        handle_create("root", desc)
                    elif current_view == "home":
                        handle_create("home", desc)
                else:
                    print("\033[1;38;5;196m[!] Snapshot creation aborted (empty description).\033[0m")
            except KeyboardInterrupt:
                pass
            
            input("\n\033[1;38;5;114mPress Enter to return to menu...\033[0m")
            continue
            
        if len(output_lines) < 2 or not snaps:
            continue

        selected_ids = []
        for line in output_lines[1:]:
            # Strip ANSI escape sequences to reliably get the ID
            clean_line = re.sub(r'\x1b\[[0-9;]*m', '', line)
            parts = clean_line.split()
            if parts:  # Defensively prevent IndexError against malformed/empty FZF returns
                sid = parts[0].strip()
                if sid in snap_map:
                    selected_ids.append(sid)

        if not selected_ids:
            continue

        # Handle Coordinated Actions
        if current_view == "coordinated":
            pairs_to_process = []
            has_error = False
            for sid in selected_ids:
                target_snap = snap_map[sid]
                print(f"\n\033[1;38;5;81m[*] Synchronizing snapshots for Root ID {sid}...\033[0m")
                try:
                    root_id, home_id = find_coordinated_pair(target_snap["raw_date"], target_snap["description"])
                    pairs_to_process.append((root_id, home_id))
                except RuntimeError as e:
                    print(f"\033[1;38;5;196m{e}\033[0m")
                    has_error = True
            
            if has_error:
                input("\n\033[1;38;5;114mPress Enter to return to menu...\033[0m")
                continue

            if key_pressed == "enter":
                if len(pairs_to_process) > 1:
                    print("\n\033[1;38;5;196m[!] Error: Cannot restore multiple snapshot pairs at once. Please select only one.\033[0m")
                    input("\033[1;38;5;114mPress Enter to return to menu...\033[0m")
                    continue
                    
                root_id, home_id = pairs_to_process[0]
                print(f"\n\033[1;38;5;81m[*] Action: COORDINATED RESTORE\033[0m")
                print(f"[*] Target Pair : Root={root_id} | Home={home_id}")
                if confirm_prompt("Are you absolutely sure you want to RESTORE your system to this state?"):
                    handle_restore_pair("root", root_id, "home", home_id)
                    break
                    
            elif key_pressed in ("ctrl-d", "delete"):
                print(f"\n\033[1;38;5;196m[*] Action: COORDINATED DELETE ({len(pairs_to_process)} pairs)\033[0m")
                for r_id, h_id in pairs_to_process:
                    print(f"[*] Target Pair : Root={r_id} | Home={h_id}")
                if confirm_prompt(f"Are you sure you want to PERMANENTLY DELETE these {len(pairs_to_process)} snapshot pair(s)?"):
                    for r_id, h_id in pairs_to_process:
                        handle_delete_pair("root", r_id, "home", h_id)
                    input("\n\033[1;38;5;114mPress Enter to return to menu...\033[0m")
                    
        # Handle Root-Only Actions
        elif current_view == "root":
            if key_pressed == "enter":
                if len(selected_ids) > 1:
                    print("\n\033[1;38;5;196m[!] Error: Cannot restore multiple snapshots at once. Please select only one.\033[0m")
                    input("\033[1;38;5;114mPress Enter to return to menu...\033[0m")
                    continue
                selected_id = selected_ids[0]
                print(f"\n\033[1;38;5;81m[*] Action: RESTORE ROOT ONLY (ID {selected_id})\033[0m")
                if confirm_prompt("Are you absolutely sure you want to RESTORE ROOT ONLY?"):
                    handle_restore("root", selected_id, False)
                    break
            elif key_pressed in ("ctrl-d", "delete"):
                print(f"\n\033[1;38;5;196m[*] Action: DELETE ROOT ONLY ({len(selected_ids)} snapshots)\033[0m")
                if confirm_prompt(f"Are you sure you want to PERMANENTLY DELETE {len(selected_ids)} Root snapshot(s)?"):
                    for sid in selected_ids:
                        handle_delete("root", sid)
                    input("\n\033[1;38;5;114mPress Enter to return to menu...\033[0m")

        # Handle Home-Only Actions
        elif current_view == "home":
            if key_pressed == "enter":
                if len(selected_ids) > 1:
                    print("\n\033[1;38;5;196m[!] Error: Cannot restore multiple snapshots at once. Please select only one.\033[0m")
                    input("\033[1;38;5;114mPress Enter to return to menu...\033[0m")
                    continue
                selected_id = selected_ids[0]
                print(f"\n\033[1;38;5;81m[*] Action: RESTORE HOME ONLY (ID {selected_id})\033[0m")
                if confirm_prompt("Are you absolutely sure you want to RESTORE HOME ONLY?"):
                    handle_restore("home", selected_id, False)
                    break
            elif key_pressed in ("ctrl-d", "delete"):
                print(f"\n\033[1;38;5;196m[*] Action: DELETE HOME ONLY ({len(selected_ids)} snapshots)\033[0m")
                if confirm_prompt(f"Are you sure you want to PERMANENTLY DELETE {len(selected_ids)} Home snapshot(s)?"):
                    for sid in selected_ids:
                        handle_delete("home", sid)
                    input("\n\033[1;38;5;114mPress Enter to return to menu...\033[0m")


def main() -> None:
    ensure_root()

    # If absolutely no arguments were passed, launch the interactive TUI
    if len(sys.argv) == 1:
        launch_tui()
        sys.exit(0)

    parser = argparse.ArgumentParser(
        description="Advanced Snapper Flat-Layout Manager & TUI for Arch Linux",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    
    parser.add_argument("-c", "--config", help="Target Snapper configuration (required for list/create/restore/delete)")
    parser.add_argument("--json", action="store_true", help="Format list output as JSON for GUI ingestion")
    parser.add_argument("--no-remount", action="store_true", help="Do not attempt a live remount after restoring a non-root subvolume")
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("-l", "--list", action="store_true", help="List snapshots for the configuration")
    group.add_argument("-C", "--create", metavar="DESC", help="Create a new snapshot with a description")
    group.add_argument("-R", "--restore", metavar="ID", help="Restore subvolume to the specified snapshot ID")
    group.add_argument("-D", "--delete", metavar="ID", help="Delete the specified snapshot ID")
    group.add_argument("--restore-pair", nargs=4, metavar=("CFG1", "ID1", "CFG2", "ID2"), help="Coordinated restore of two configs on the same Btrfs filesystem")
    group.add_argument("--delete-pair", nargs=4, metavar=("CFG1", "ID1", "CFG2", "ID2"), help="Coordinated deletion of two snapshots")
    group.add_argument("--sync-restore", nargs="+", metavar="ARGS", help="Automatically match and stage a coordinated restore (Usage: TARGET_DATE [TARGET_DESC])")
    group.add_argument("--sync-delete", nargs="+", metavar="ARGS", help="Automatically match and perform a coordinated deletion (Usage: TARGET_DATE [TARGET_DESC])")

    args = parser.parse_args()

    # Require -c/--config for single-target actions
    if (args.list or args.create is not None or args.restore is not None or args.delete is not None) and not args.config:
        parser.error("-c/--config is required with --list, --create, --restore, and --delete")

    # CLI Execution Paths
    if args.list:
        handle_list(args.config, args.json)
    elif args.create is not None:
        handle_create(args.config, args.create)
    elif args.restore is not None:
        handle_restore(args.config, args.restore, args.no_remount)
    elif args.delete is not None:
        handle_delete(args.config, args.delete)
    elif args.delete_pair is not None:
        handle_delete_pair(*args.delete_pair)
    elif args.restore_pair is not None:
        handle_restore_pair(*args.restore_pair)
    elif args.sync_restore is not None:
        if len(args.sync_restore) < 1 or len(args.sync_restore) > 2:
            parser.error("--sync-restore requires 1 or 2 arguments: TARGET_DATE [TARGET_DESC]")
        target_date = args.sync_restore[0]
        target_desc = args.sync_restore[1] if len(args.sync_restore) == 2 else None
        
        try:
            root_id, home_id = find_coordinated_pair(target_date, target_desc)
            print(f"[*] Found coordinated snapshot pair: Root={root_id} Home={home_id}", file=sys.stderr)
            handle_restore_pair("root", root_id, "home", home_id)
        except RuntimeError as e:
            fail(str(e))
        
    elif args.sync_delete is not None:
        if len(args.sync_delete) < 1 or len(args.sync_delete) > 2:
            parser.error("--sync-delete requires 1 or 2 arguments: TARGET_DATE [TARGET_DESC]")
        target_date = args.sync_delete[0]
        target_desc = args.sync_delete[1] if len(args.sync_delete) == 2 else None
        
        try:
            root_id, home_id = find_coordinated_pair(target_date, target_desc)
            print(f"[*] Found coordinated snapshot pair: Root={root_id} Home={home_id}", file=sys.stderr)
            handle_delete_pair("root", root_id, "home", home_id)
        except RuntimeError as e:
            fail(str(e))


if __name__ == "__main__":
    # --- Asynchronous TUI Preview Payload Interception ---
    # Intercepts the FZF subshell request *before* argparse or privileges are evaluated,
    # ensuring blindingly fast execution for the `snapper status` preview panel.
    if len(sys.argv) >= 3 and sys.argv[1] == "--tui-preview":
        _view = sys.argv[2]
        _show_diff = "--show-diff" in sys.argv
        _remaining = [a for a in sys.argv[3:] if a != "--show-diff"]
        _line = " ".join(_remaining)
        handle_tui_preview(_view, _line, show_diff=_show_diff)
        sys.exit(0)

    main()
