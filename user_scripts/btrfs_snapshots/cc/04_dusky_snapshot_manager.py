#!/usr/bin/env python3
"""
Advanced Btrfs/Snapper Flat Layout Manager (snapctl)
Engineered for strict safety and coordinated subvolume swapping on Arch Linux.
"""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def fail(message: str, exit_code: int = 1) -> None:
    print(message, file=sys.stderr)
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


def get_btrfs_device(mountpoint: str) -> str:
    result = run_cmd(["findmnt", "--fstab", "--evaluate", "-n", "-o", "SOURCE", "--target", mountpoint])
    device = result.stdout.strip()
    if not device.startswith("/dev/"):
        fail(f"[!] Fatal: Could not resolve physical block device for {mountpoint}. Found: {device}")
    return os.path.realpath(device)


def get_subvol_from_fstab(mountpoint: str) -> str:
    result = run_cmd(["findmnt", "--fstab", "-n", "-o", "OPTIONS", "--target", mountpoint])
    options = result.stdout.strip()
    match = re.search(r"(?:^|,)subvol=([^,]+)(?:,|$)", options)
    if not match:
        fail(f"[!] Fatal: No 'subvol=' option found in fstab for {mountpoint}.")
    return match.group(1).lstrip("/")


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
        print(f"[*] Mounting top-level tree (subvolid=5) for {device}...", file=sys.stderr)
        run_cmd(["mount", "-o", "subvolid=5", device, str(mnt_point)])

        active_exception: BaseException | None = None
        try:
            yield mnt_point
        except BaseException as exc:
            active_exception = exc
            raise
        finally:
            print("[*] Unmounting top-level tree...", file=sys.stderr)
            result = run_cmd(["umount", str(mnt_point)], check=False)
            if result.returncode != 0:
                message = error_text(result)
                if active_exception is None:
                    fail(f"[!] Command failed: umount {mnt_point}\n{message}", result.returncode)
                print(f"[!] Warning: Failed to unmount top-level tree {mnt_point}: {message}", file=sys.stderr)


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
    backup_path: Path
    staging_path: Path
    staging_created: bool = False
    active_moved: bool = False
    activated: bool = False


def resolve_restore_spec(config: str, snap_id: str) -> RestoreSpec:
    snap_id = validate_snapshot_id(snap_id)
    target_mnt = get_target_mount_from_snapper_config(config)
    snapshots_mnt = "/.snapshots" if target_mnt == "/" else f"{target_mnt}/.snapshots"
    device = get_btrfs_device(target_mnt)
    active_subvol = get_subvol_from_fstab(target_mnt)
    snapshots_subvol = get_subvol_from_fstab(snapshots_mnt)

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
    backup_path = target_path.with_name(f"{target_path.name}_backup_{timestamp}")
    staging_path = target_path.with_name(f"{target_path.name}_restore_{spec.snap_id}_{timestamp}")

    return PreparedRestore(
        spec=spec,
        source_snapshot=source_snapshot,
        target_path=target_path,
        backup_path=backup_path,
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
            f"[!] An atomic rollback would trap these inside the backup subvolume.\n"
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
        if plan.active_moved and plan.backup_path.exists() and not plan.target_path.exists():
            try:
                plan.backup_path.rename(plan.target_path)
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
        if plan.backup_path.exists():
            fail(
                f"[!] Fatal: Backup path already exists for config "
                f"'{plan.spec.config}': {plan.backup_path}"
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
                f"[*] Creating staged restore subvolume for '{plan.spec.config}': "
                f"{plan.staging_path.name}..."
            )
            run_cmd_raise(
                ["btrfs", "subvolume", "snapshot", str(plan.source_snapshot), str(plan.staging_path)]
            )
            plan.staging_created = True

        for plan in plans:
            print(
                f"[*] Moving active subvolume for '{plan.spec.config}' to "
                f"{plan.backup_path.name}..."
            )
            plan.target_path.rename(plan.backup_path)
            plan.active_moved = True

        for plan in plans:
            print(
                f"[*] Activating restored snapshot for '{plan.spec.config}' as "
                f"{plan.target_path.name}..."
            )
            plan.staging_path.rename(plan.target_path)
            plan.activated = True

    except (OSError, RuntimeError) as exc:
        rollback_prepared_restores(plans, exc)


def is_mountpoint(path: str) -> bool:
    result = run_cmd(["mountpoint", "-q", "--", path], check=False)
    return result.returncode == 0


def activate_nonroot_restore(target_mnt: str) -> None:
    if not is_mountpoint(target_mnt):
        print(
            f"[*] {target_mnt} is not currently mounted as its own mountpoint. "
            f"Restored subvolume will be used on the next mount."
        )
        return

    print(f"[*] Remounting {target_mnt} to activate restored snapshot...")

    umount_result = run_cmd(["umount", target_mnt], check=False)
    if umount_result.returncode != 0:
        fail(
            f"[!] Restore completed on disk, but {target_mnt} could not be unmounted for live activation.\n"
            f"{error_text(umount_result)}\n"
            f"[!] Reboot or manually unmount/remount {target_mnt} to use the restored snapshot."
        )

    mount_result = run_cmd(["mount", target_mnt], check=False)
    if mount_result.returncode != 0:
        fail(
            f"[!] Restore completed on disk, but remount of {target_mnt} failed.\n"
            f"{error_text(mount_result)}\n"
            f"[!] Do not continue until {target_mnt} is mounted again or the restore is corrected."
        )

    print(f"[+] {target_mnt} successfully remounted.")


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


def format_snapshot_date(raw_value: object) -> str:
    if raw_value is None:
        return ""

    if isinstance(raw_value, int | float):
        try:
            return datetime.fromtimestamp(raw_value).strftime("%m/%d/%y %I:%M %p")
        except (OverflowError, OSError, ValueError):
            return str(raw_value)

    raw = str(raw_value).strip()
    if not raw:
        return raw

    iso_candidates = [raw]
    if " " in raw:
        iso_candidates.append(raw.replace(" ", "T", 1))

    for candidate in iso_candidates:
        try:
            return datetime.fromisoformat(candidate).strftime("%m/%d/%y %I:%M %p")
        except ValueError:
            pass

    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, pattern).strftime("%m/%d/%y %I:%M %p")
        except ValueError:
            continue

    tokens = raw.split()
    if len(tokens) >= 7 and tokens[-1].isalpha():
        try:
            clean_date = " ".join(tokens[:-1])
            return datetime.strptime(clean_date, "%a %d %b %Y %I:%M:%S %p").strftime("%m/%d/%y %I:%M %p")
        except ValueError:
            pass

    return raw


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

        gui_data.append(
            {
                "id": snap_id,
                "type": str(first_present(record, "type", "snapshot_type") or ""),
                "date": format_snapshot_date(raw_date_value),
                "raw_date": raw_date,
                "description": str(first_present(record, "description", "desc") or ""),
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

        raw_date = parts[3]
        description = "|".join(parts[6:]).strip()

        gui_data.append(
            {
                "id": snap_id,
                "type": parts[1],
                "date": format_snapshot_date(raw_date),
                "raw_date": raw_date,
                "description": description,
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


def handle_list(config: str, as_json: bool) -> None:
    if not as_json:
        sys.exit(run_passthrough(["snapper", "-c", config, "list"]))

    print(json.dumps(load_snapshot_list_for_gui(config), ensure_ascii=False))


def handle_create(config: str, description: str) -> None:
    print(f"[*] Creating snapshot for '{config}': {description}")
    run_cmd(["snapper", "-c", config, "create", "-d", description])
    print("[+] Snapshot created successfully.")


def handle_restore(config: str, snap_id: str, no_remount: bool) -> None:
    spec = resolve_restore_spec(config, snap_id)

    with mount_top_level(spec.device) as top_mnt:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        plan = prepare_restore(spec, top_mnt, timestamp)
        apply_prepared_restores([plan])

    print("\n[+] Restoration complete.")
    if spec.target_mnt == "/":
        print("\n[!] ROOT FILESYSTEM RESTORED. You MUST reboot immediately for changes to take effect.")
        return

    if no_remount:
        print(
            f"[!] {spec.target_mnt} was restored on disk without live remount.\n"
            f"[!] Reboot or manually remount {spec.target_mnt} to activate the restored snapshot."
        )
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

    print("\n[+] Coordinated restoration complete.")
    if spec1.target_mnt == "/" or spec2.target_mnt == "/":
        print("\n[!] Reboot required before the coordinated restore takes effect.")
    else:
        print(
            "\n[!] Restored subvolumes were staged on disk without live remount.\n"
            "[!] Manually remount them or reboot before use."
        )


def handle_delete(config: str, snap_id: str) -> None:
    snap_id = validate_snapshot_id(snap_id)
    if snap_id == "0":
        fail(f"[!] Fatal: Cannot delete snapshot ID 0 (the active system state) for config '{config}'.")
    
    print(f"[*] Deleting snapshot ID {snap_id} for '{config}'...")
    run_cmd(["snapper", "-c", config, "delete", snap_id])
    print(f"[+] Snapshot ID {snap_id} deleted successfully.")


def handle_delete_pair(config1: str, snap_id1: str, config2: str, snap_id2: str) -> None:
    if config1 == config2:
        fail("[!] Fatal: Coordinated deletion requires two distinct snapper configs.")
        
    handle_delete(config1, snap_id1)
    handle_delete(config2, snap_id2)
    print("\n[+] Coordinated deletion complete.")


def main() -> None:
    if os.geteuid() != 0:
        fail("[!] This script requires root privileges. Please run with sudo.")

    parser = argparse.ArgumentParser(
        description="Advanced Snapper Flat-Layout Manager for Arch Linux",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "-c",
        "--config",
        help="Target Snapper configuration (required for list/create/restore/delete)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Format list output as JSON for GUI ingestion",
    )
    parser.add_argument(
        "--no-remount",
        action="store_true",
        help="Do not attempt a live remount after restoring a non-root subvolume",
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("-l", "--list", action="store_true", help="List snapshots for the configuration")
    group.add_argument("-C", "--create", metavar="DESC", help="Create a new snapshot with a description")
    group.add_argument("-R", "--restore", metavar="ID", help="Restore subvolume to the specified snapshot ID")
    group.add_argument("-D", "--delete", metavar="ID", help="Delete the specified snapshot ID")
    group.add_argument(
        "--restore-pair",
        nargs=4,
        metavar=("CFG1", "ID1", "CFG2", "ID2"),
        help="Coordinated restore of two configs on the same Btrfs filesystem",
    )
    group.add_argument(
        "--delete-pair",
        nargs=4,
        metavar=("CFG1", "ID1", "CFG2", "ID2"),
        help="Coordinated deletion of two snapshots",
    )

    args = parser.parse_args()

    # Require -c/--config for single-target actions
    if (args.list or args.create is not None or args.restore is not None or args.delete is not None) and not args.config:
        parser.error("-c/--config is required with --list, --create, --restore, and --delete")

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
    else:
        handle_restore_pair(*args.restore_pair)


if __name__ == "__main__":
    main()
