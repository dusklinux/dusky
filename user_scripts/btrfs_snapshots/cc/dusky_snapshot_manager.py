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
                f"\033[1;38;5;81m[*] Creating staged restore subvolume for '{plan.spec.config}': "
                f"{plan.staging_path.name}...\033[0m"
            )
            run_cmd_raise(
                ["btrfs", "subvolume", "snapshot", str(plan.source_snapshot), str(plan.staging_path)]
            )
            plan.staging_created = True

        for plan in plans:
            print(
                f"\033[1;38;5;81m[*] Moving active subvolume for '{plan.spec.config}' to "
                f"{plan.backup_path.name}...\033[0m"
            )
            plan.target_path.rename(plan.backup_path)
            plan.active_moved = True

        for plan in plans:
            print(
                f"\033[1;38;5;81m[*] Activating restored snapshot for '{plan.spec.config}' as "
                f"{plan.target_path.name}...\033[0m"
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
            f"\033[1;38;5;81m[*] {target_mnt} is not currently mounted as its own mountpoint. "
            f"Restored subvolume will be used on the next mount.\033[0m"
        )
        return

    print(f"\033[1;38;5;81m[*] Remounting {target_mnt} to activate restored snapshot...\033[0m")

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

    print(f"\033[1;38;5;114m[+] {target_mnt} successfully remounted.\033[0m")


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
        # Strip all ANSI escape sequences to process raw FZF line natively
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        clean_line = ansi_escape.sub('', line)
        parts = [p.strip() for p in clean_line.split("│")]
        
        if not parts or not parts[0].isdigit():
            print("\n\033[3;38;5;246m  No snapshot selected or invalid layout.\033[0m")
            return
            
        snap_id = parts[0]
        snap_type = parts[1] if len(parts) > 1 else "Unknown"
        snap_date = parts[3] if len(parts) > 3 else "Unknown"
        snap_desc = parts[4] if len(parts) > 4 else "No Description"
        
        # 1. Cleanly Aligned Shortcuts Panel
        print("\033[1;38;5;220m╭─ 󰏖 KEYBOARD SHORTCUTS \033[38;5;238m" + "─"*30 + "\033[1;38;5;220m╮\033[0m")
        print("\033[1;38;5;220m│\033[0m \033[1;38;5;114m[ENTER]\033[0m   \033[38;5;253mRestore Selected\033[0m" + " "*26 + "\033[1;38;5;220m│\033[0m")
        print("\033[1;38;5;220m│\033[0m \033[1;38;5;196m[DEL]\033[0m     \033[38;5;253mDelete Selected\033[0m" + " "*27 + "\033[1;38;5;220m│\033[0m")
        print("\033[1;38;5;220m│\033[0m \033[1;38;5;81m[CTRL-S]\033[0m  \033[38;5;253mCreate New Snapshot\033[0m" + " "*23 + "\033[1;38;5;220m│\033[0m")
        print("\033[1;38;5;220m│\033[0m \033[1;38;5;213m[TAB]\033[0m     \033[38;5;253mSwitch View (Root/Home)\033[0m" + " "*19 + "\033[1;38;5;220m│\033[0m")
        print("\033[1;38;5;220m│\033[0m \033[1;38;5;246m[CTRL-A]\033[0m  \033[38;5;253mSelect All\033[0m" + " "*32 + "\033[1;38;5;220m│\033[0m")
        print("\033[1;38;5;220m│\033[0m \033[1;38;5;246m[CTRL-X]\033[0m  \033[38;5;253mDeselect All\033[0m" + " "*30 + "\033[1;38;5;220m│\033[0m")
        print("\033[1;38;5;220m╰" + "─"*53 + "╯\033[0m\n")

        # 2. Snapshot Meta Data
        print(f"\033[1;38;5;81m󰆑 SNAPSHOT DETAILS\033[0m")
        print(f"\033[38;5;238m" + "─" * 55 + "\033[0m")
        print(f" \033[1;38;5;246mConfig\033[0m │ \033[1;38;5;253m{view.upper()}\033[0m")
        print(f" \033[1;38;5;246mID    \033[0m │ \033[1;38;5;39m{snap_id}\033[0m")
        print(f" \033[1;38;5;246mType  \033[0m │ \033[38;5;213m{snap_type}\033[0m")
        print(f" \033[1;38;5;246mDate  \033[0m │ \033[38;5;220m{snap_date}\033[0m")
        print(f" \033[1;38;5;246mDesc  \033[0m │ \033[38;5;253m{snap_desc}\033[0m\n")

        # 3. Dynamic Diff generation via snapper status (Snapshot -> Current)
        # Only runs when explicitly requested via Ctrl+V for instant UI responsiveness
        if show_diff:
            print(f"\033[1;38;5;114m󰏫 FILES CHANGED IF RESTORED\033[0m \033[3;38;5;246m(vs Current System)\033[0m")
            print(f"\033[38;5;238m" + "─" * 55 + "\033[0m")

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

    views = ["coordinated", "root", "home"]
    view_idx = 0

    # Catppuccin Mocha vivid theme styling extracted from pkg reference
    fzf_colors = (
        "bg+:#1e1e2e,bg:#11111b,spinner:#f5e0dc,"
        "fg:#cdd6f4,fg+:#cdd6f4,header:#89b4fa,info:#cba6f7,"
        "pointer:#a6e3a1,marker:#f5e0dc,prompt:#cba6f7,"
        "hl:#f38ba8,hl+:#f38ba8,border:#585b70,label:#a6e3a1"
    )

    executable = shlex.quote(sys.executable)
    script_path = shlex.quote(os.path.abspath(sys.argv[0]))

    while True:
        current_view = views[view_idx]
        
        # Fetch instantaneous Global Btrfs Storage usage natively
        gb = 1024**3
        total, used, free = shutil.disk_usage("/")
        
        # Vivid Nerd Font System Storage Header
        storage_hdr = f" \033[1;38;5;81m󰋊 BTRFS STORAGE:\033[0m \033[38;5;253m{total/gb:.1f} GB Total\033[0m \033[38;5;238m|\033[0m \033[38;5;203m{used/gb:.1f} GB Used\033[0m \033[38;5;238m|\033[0m \033[38;5;114m{free/gb:.1f} GB Free\033[0m "
        
        config_to_query = "root" if current_view in ("coordinated", "root") else "home"
        snaps = load_snapshot_list_for_gui(config_to_query)
        
        lines_for_fzf = []
        snap_map = {}
        c_sep = "\033[38;5;238m│\033[0m"
        
        if snaps:
            snap_map = {s["id"]: s for s in snaps}
            snaps_sorted = sorted(snaps, key=lambda x: int(x["id"]), reverse=True)

            for s in snaps_sorted:
                dt = parse_snapshot_datetime(s["raw_date"])
                age_str = time_ago(dt) if dt else "Unknown"
                
                # Highly vivid 256-color column styling matching 'pkg' Atlas logic
                id_str = f"\033[1;38;5;39m{s['id']:>4}\033[0m"         # Deep Sky Blue
                type_str = f"\033[38;5;213m{s['type']:<7}\033[0m"       # Pink
                age_colored = f"\033[38;5;114m{age_str:<10}\033[0m"    # Pale Green
                date_str = f"\033[38;5;220m{s['date']:<18}\033[0m"     # Gold
                desc_str = f"\033[38;5;253m{s['description']}\033[0m"  # Crisp White
                
                lines_for_fzf.append(f"{id_str} {c_sep} {type_str} {c_sep} {age_colored} {c_sep} {date_str} {c_sep} {desc_str}")
        else:
            lines_for_fzf.append(f"\033[1;38;5;196m No snapshots found for '{config_to_query}' configuration.\033[0m")
        
        if current_view == "coordinated":
            mode_hdr = f" \033[1;38;5;213m󰑐 VIEW: ROOT+HOME (Coordinated)\033[0m"
        elif current_view == "root":
            mode_hdr = f" \033[1;38;5;39m󰒋 VIEW: ROOT ONLY\033[0m"
        else:
            mode_hdr = f" \033[1;38;5;114m󰋜 VIEW: HOME ONLY\033[0m"
        
        # Static Matrix Header Table
        table_hdr = f"  \033[1;38;5;242mID\033[0m   {c_sep} \033[1;38;5;242mTYPE\033[0m    {c_sep} \033[1;38;5;242mAGE\033[0m        {c_sep} \033[1;38;5;242mDATE\033[0m               {c_sep} \033[1;38;5;242mDESCRIPTION\033[0m"
        hr_width = min(80, shutil.get_terminal_size().columns - 4)
        hr = "\033[38;5;238m" + "─" * hr_width + "\033[0m"
        
        # Compile Ultra-Clean FZF Top Header
        header = f"{storage_hdr}\n{mode_hdr}\n{hr}\n{table_hdr}"

        # Utilizing strict fzf 0.73.1 syntax and layout logic for async previewing
        preview_cmd = f"{executable} {script_path} --tui-preview {current_view} {{}}"
        preview_diff_cmd = f"{executable} {script_path} --tui-preview {current_view} --show-diff {{}}"

        fzf_cmd = [
            "fzf",
            "--multi",
            "--ansi",
            "--reverse",
            "--header", header,
            "--header-border=horizontal",
            "--border=rounded",
            "--prompt= :: Time Machine ❯ ",
            f"--color={fzf_colors}",
            "--pointer=",
            "--marker=✓",
            "--no-hscroll",
            "--ellipsis=",
            "--expect=enter,ctrl-d,delete,tab,ctrl-s,alt-s",
            f"--bind=ctrl-a:select-all,ctrl-x:deselect-all,ctrl-space:toggle,shift-down:toggle+down,shift-up:toggle+up,ctrl-p:toggle-preview,ctrl-v:change-preview({preview_diff_cmd})+change-prompt( :: Diff Mode ON (Slower) ❯ ),ctrl-b:change-preview({preview_cmd})+change-prompt( :: Time Machine ❯ )",
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
