#!/usr/bin/env python3
"""
Dusky BTRFS & Snapper Master Controller
Engineered for strict safety, coordinated subvolume swapping, external backups,
NOCOW management, and an interactive 5-tab TUI on Arch Linux.
"""

import argparse
import csv
import json
import logging
import logging.handlers
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
from typing import Any

# Modern Type Aliases (Python 3.12+ / 3.14+ Compatible)
type BtrfsMount = dict[str, Any]
type SubvolMeta = dict[str, Any]


# =============================================================================
# LOGGING (Systemd Journal & Flat File Integration)
# =============================================================================

def setup_logger() -> logging.Logger:
    """
    Initializes an Arch-compliant logger that writes to the systemd journal
    and a fallback flat file. This prevents TUI tearing while preserving critical errors.
    """
    logger = logging.getLogger("dusky-master")
    logger.setLevel(logging.INFO)
    logger.propagate = False # Prevent leaking to stdout/stderr (protects FZF)

    # 1. Native Systemd Journal routing via /dev/log
    try:
        syslog_handler = logging.handlers.SysLogHandler(address='/dev/log')
        syslog_handler.setFormatter(logging.Formatter('dusky-master[%(process)d]: %(levelname)s - %(message)s'))
        logger.addHandler(syslog_handler)
    except Exception:
        pass

    # 2. Hardened Flat-file backup for easy grepping
    try:
        file_handler = logging.FileHandler("/var/log/dusky.log")
        file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
        logger.addHandler(file_handler)
    except OSError:
        pass

    return logger

LOG = setup_logger()


# =============================================================================
# CORE SYSTEM UTILITIES
# =============================================================================

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
    LOG.critical(message)
    sys.exit(exit_code)

def error_text(result: subprocess.CompletedProcess[str]) -> str:
    return result.stderr.strip() or result.stdout.strip() or "<no error output>"

def run_cmd(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        fail(f"[!] Command execution failed: {shlex.join(cmd)}\n{exc}")

    if check and result.returncode != 0:
        fail(f"[!] Command failed: {shlex.join(cmd)}\n{error_text(result)}", result.returncode)
    return result

def run_cmd_raise(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
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


# =============================================================================
# BTRFS & SNAPPER RESOLUTION LOGIC
# =============================================================================

def get_btrfs_device(mountpoint: str) -> str:
    result = run_cmd(["findmnt", "-n", "-v", "-e", "-o", "SOURCE", "-M", mountpoint])
    device = result.stdout.strip()
    if not device.startswith("/dev/"):
        fail(f"[!] Fatal: Could not resolve physical block device for {mountpoint}. Found: {device}")
    return os.path.realpath(device)

def get_active_subvol(mountpoint: str) -> str:
    result = run_cmd(["findmnt", "--fstab", "-n", "-o", "OPTIONS", "-M", mountpoint], check=False)
    if result.returncode == 0:
        match = re.search(r"(?:^|,)subvol=([^,]+)(?:,|$)", result.stdout.strip())
        if match: return match.group(1).lstrip("/")

    result = run_cmd(["findmnt", "-n", "-o", "OPTIONS", "-M", mountpoint], check=False)
    if result.returncode == 0:
        match = re.search(r"(?:^|,)subvol=([^,]+)(?:,|$)", result.stdout.strip())
        if match: return match.group(1).lstrip("/")

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
            if target_mnt: return target_mnt
            break
    fail(f"[!] Fatal: Could not determine SUBVOLUME for snapper config '{config}'.")

def validate_snapshot_id(snap_id: str) -> str:
    snap_id = snap_id.strip()
    if not snap_id.isdigit():
        fail(f"[!] Fatal: Invalid snapshot ID: {snap_id!r}")
    return snap_id

def get_snapper_configs() -> list[dict[str, str]]:
    configs = []
    config_dir = Path("/etc/snapper/configs")
    if config_dir.is_dir():
        for cfg_file in config_dir.iterdir():
            if cfg_file.is_file() and not cfg_file.name.startswith('.'):
                cfg_name = cfg_file.name
                sub = "/"
                try:
                    content = cfg_file.read_text(errors="ignore")
                    match = re.search(r'^SUBVOLUME="?([^"\n]+)"?', content, re.MULTILINE)
                    if match: sub = match.group(1)
                except Exception: pass
                configs.append({"config": cfg_name, "subvolume": sub})
        if configs: return configs

    res = run_cmd(["snapper", "--csvout", "--no-headers", "list-configs"], check=False)
    if res.returncode == 0:
        reader = csv.reader(res.stdout.splitlines())
        for row in reader:
            if len(row) >= 2:
                cfg = row[0].strip()
                sub = row[1].strip()
                if cfg: configs.append({"config": cfg, "subvolume": sub})
    return configs


# =============================================================================
# TOPOLOGY, NOCOW & EXTERNAL BACKUP (BTRFS SEND/RECEIVE)
# =============================================================================

@contextmanager
def mount_top_level(device: str, quiet: bool = False) -> Iterator[Path]:
    """
    Context manager to mount the physical top-level BTRFS tree.
    Protected with a polled retry loop to guarantee native unmount capability
    without leaking zombie mounts via lazy unmounts.
    """
    with tempfile.TemporaryDirectory(prefix="btrfs_top_level_", dir="/mnt", ignore_cleanup_errors=True) as tmpdir:
        mnt_point = Path(tmpdir)
        if not quiet:
            print(f"\033[1;38;5;81m[*] Mounting top-level tree (subvolid=5) for {device}...\033[0m", file=sys.stderr)
        
        res = run_cmd(["mount", "-o", "subvolid=5", device, str(mnt_point)], check=False)
        if res.returncode != 0:
            if quiet:
                LOG.error(f"Background mount failed for {device}: {error_text(res)}")
                raise RuntimeError(f"Failed to mount {device}: {error_text(res)}")
            else:
                fail(f"[!] Command failed: mount {mnt_point}\n{error_text(res)}", res.returncode)

        active_exception: BaseException | None = None
        try:
            yield mnt_point
        except BaseException as exc:
            active_exception = exc
            raise
        finally:
            if not quiet:
                print("\033[1;38;5;81m[*] Unmounting top-level tree...\033[0m", file=sys.stderr)
            
            # [SURGICAL FIX] Native Polled Retry Loop - Eliminates zombie mounts caused by umount -l
            unmounted = False
            for attempt in range(3):
                result = run_cmd(["umount", str(mnt_point)], check=False)
                if result.returncode == 0:
                    unmounted = True
                    break
                time.sleep(1)
                
            if not unmounted:
                message = error_text(result)
                log_msg = f"Failed to cleanly unmount {mnt_point} after 3 attempts: {message}. Filesystem may be busy."
                
                if quiet:
                    LOG.warning(log_msg)
                else:
                    if active_exception is None:
                        fail(f"[!] Command failed: umount {mnt_point}\n{message}", result.returncode)
                    print(f"\033[1;38;5;220m[!] Warning: {log_msg}\033[0m", file=sys.stderr)

def get_btrfs_mounts() -> list[BtrfsMount]:
    res = run_cmd(["findmnt", "-t", "btrfs", "-J", "-e"], check=False)
    if res.returncode != 0 or not res.stdout.strip(): return []
    try: return json.loads(res.stdout).get("filesystems", [])
    except json.JSONDecodeError: return []

def get_all_subvolumes() -> list[SubvolMeta]:
    """
    Optimized O(1) memory lookup for UI load speeds.
    Synchronous physical IO checks have been completely removed from this phase
    and shifted to the Action handlers to guarantee rapid application launches.
    """
    mounts = get_btrfs_mounts()
    seen_devs = set()
    subvols: list[SubvolMeta] = []
    subvol_regex = re.compile(r"^ID\s+(\d+).*?path\s+(.+)$")
    
    for m in mounts:
        raw_dev = m.get("source")
        if not raw_dev: continue
        
        # findmnt annotates btrfs sources with subvolume info (e.g. /dev/nvme0n1p2[/@]).
        # Strip the [/subvol] bracket notation so mount(8) receives a valid block device path.
        dev = os.path.realpath(re.sub(r'\[.*?\]', '', raw_dev))
        if dev in seen_devs: continue
        seen_devs.add(dev)
        
        target_hint = m.get("target", "/")
        
        try:
            with mount_top_level(dev, quiet=True) as top_mnt:
                res = run_cmd(["btrfs", "subvolume", "list", str(top_mnt)], check=False)
                if res.returncode != 0: continue
                    
                # Read-Only status lookup via -r flag
                ro_res = run_cmd(["btrfs", "subvolume", "list", "-r", str(top_mnt)], check=False)
                ro_ids = set()
                if ro_res.returncode == 0:
                    for line in ro_res.stdout.splitlines():
                        match = subvol_regex.match(line.strip())
                        if match: ro_ids.add(match.group(1))

                for line in res.stdout.splitlines():
                    match = subvol_regex.match(line.strip())
                    if match:
                        sv_id = match.group(1)
                        sv_path = match.group(2).strip()
                        
                        # UX Optimization: Hide Snapper snapshots from the native Subvolumes tab
                        if "/.snapshots/" in sv_path and sv_path.endswith("/snapshot"):
                            continue
                        
                        # Existence verification is now deferred precisely until an action is selected.
                        subvols.append({
                            "id": sv_id,
                            "path": sv_path,
                            "device": dev,
                            "mount_target": target_hint,
                            "is_ro": sv_id in ro_ids
                        })
        except Exception as e:
            LOG.error(f"Subvolume scan encountered an error on device '{dev}': {e}")
            pass
            
    return subvols

def create_nocow_subvolume(parent_dir: str, name: str, disable_cow: bool) -> None:
    full_path = Path(parent_dir) / name
    if full_path.exists(): fail(f"[!] Target path already exists: {full_path}")
        
    print(f"\033[1;38;5;81m[*] Creating BTRFS subvolume at {full_path}...\033[0m")
    run_cmd(["btrfs", "subvolume", "create", str(full_path)])
    
    if disable_cow:
        print(f"\033[1;38;5;220m[*] Applying NOCOW attribute (chattr +C)...\033[0m")
        run_cmd(["chattr", "+C", str(full_path)])
        print("\033[1;38;5;114m[+] Subvolume created with Copy-On-Write DISABLED.\033[0m")
    else:
        print("\033[1;38;5;114m[+] Subvolume created (Standard COW).\033[0m")

def backup_snapshot_to_external(src_dev: str, src_rel_path: str, external_dest: str) -> None:
    dest_path = Path(external_dest)
    
    if not dest_path.is_dir(): 
        fail(f"[!] External destination does not exist or is not a directory: {dest_path}")
    fs_check = run_cmd(["stat", "-f", "-c", "%T", str(dest_path)], check=False)
    if fs_check.returncode != 0 or "btrfs" not in fs_check.stdout.lower(): 
        fail(f"[!] Destination {dest_path} is not recognized as a BTRFS filesystem.")

    print(f"\033[1;38;5;81m[*] Resolving physical block device for external drive at {dest_path}...\033[0m")
    ext_dev = get_btrfs_device(str(dest_path))
    
    with mount_top_level(src_dev) as src_mnt, mount_top_level(ext_dev) as ext_mnt:
        
        # Self-Healing Sweep: Clean up orphaned snapshots from previous power-losses
        print(f"\033[38;5;246m[*] Sweeping for orphaned ephemeral backups...\033[0m")
        for item in src_mnt.iterdir():
            if item.name.startswith(".tmp_send_") and item.is_dir():
                run_cmd(["btrfs", "subvolume", "delete", str(item)], check=False)
                
        src_path = src_mnt / src_rel_path.lstrip("/")
        if not src_path.exists():
            fail(f"[!] Source subvolume could not be resolved at the physical layer: {src_path}")

        ro_check = run_cmd(["btrfs", "property", "get", "-t", "subvol", str(src_path), "ro"], check=False)
        is_ro = "ro=true" in ro_check.stdout.lower()

        ephemeral_snap = None
        if not is_ro:
            print(f"\033[1;38;5;220m[*] Source {src_path.name or 'root'} is writable. Creating ephemeral Read-Only snapshot for secure stream...\033[0m")
            # Create snapshot firmly at physical top level to avoid parent bounds traversing
            ephemeral_snap = src_mnt / f".tmp_send_{src_path.name or 'root'}_{int(time.time())}"
            run_cmd_raise(["btrfs", "subvolume", "snapshot", "-r", str(src_path), str(ephemeral_snap)])
            src_path = ephemeral_snap
            
        try:
            staging_dir = Path(tempfile.mkdtemp(dir=ext_mnt, prefix=".btrfs_recv_"))
            try:
                print(f"\033[38;5;246mExecuting stream: btrfs send {src_path} | btrfs receive {staging_dir}\033[0m")
                with subprocess.Popen(["btrfs", "send", str(src_path)], stdout=subprocess.PIPE) as send_proc:
                    recv_proc = subprocess.run(["btrfs", "receive", str(staging_dir)], stdin=send_proc.stdout, capture_output=True, text=True)
                    send_proc.wait() 
                    
                if send_proc.returncode != 0 or recv_proc.returncode != 0: 
                    fail(f"[!] Send/Receive stream failed:\n{recv_proc.stderr}")
                    
                received_items = list(staging_dir.iterdir())
                if not received_items: 
                    fail("[!] Stream completed but no subvolume was found in staging.")
                    
                src_item = received_items[0]
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                original_name = Path(src_rel_path).name or "root"
                final_dest = ext_mnt / f"backup_snap_{original_name}_{timestamp}"
                
                if final_dest.exists():
                    fail(f"[!] Fatal: Target top-level subvolume already exists: {final_dest}")
                    
                src_item.rename(final_dest)
                print(f"\033[1;38;5;114m[+] Backup successful!\033[0m")
                print(f"\033[1;38;5;114m[+] Top-level Subvolume safely created at: {ext_dev} -> {final_dest.name}\033[0m")
            finally:
                # Prevent garbage resource leaks from failed receives
                if staging_dir.exists():
                    try:
                        for item in staging_dir.iterdir():
                            # Must explicitly execute Btrfs native deletion for incomplete subvolumes
                            run_cmd(["btrfs", "subvolume", "delete", str(item)], check=False)
                        staging_dir.rmdir()
                    except OSError as e:
                        LOG.error(f"Post-backup cleanup warning for staging directory {staging_dir}: {e}")
        finally:
            if ephemeral_snap and ephemeral_snap.exists():
                print(f"\033[38;5;246m[*] Cleaning up ephemeral snapshot...\033[0m")
                run_cmd(["btrfs", "subvolume", "delete", str(ephemeral_snap)], check=False)


# =============================================================================
# SNAPPER ROLLBACK ENGINE & RESTORATION STAGING
# =============================================================================

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

    if not active_subvol: fail(f"[!] Fatal: Empty active subvolume path is not supported for {target_mnt}.")
    if not snapshots_subvol: fail(f"[!] Fatal: Empty snapshots subvolume path is not supported for {snapshots_mnt}.")

    return RestoreSpec(config, snap_id, target_mnt, device, active_subvol, snapshots_subvol)

def prepare_restore(spec: RestoreSpec, top_mnt: Path, timestamp: str) -> PreparedRestore:
    target_path = top_mnt / spec.active_subvol.lstrip("/")
    source_snapshot = top_mnt / spec.snapshots_subvol.lstrip("/") / spec.snap_id / "snapshot"
    temp_delete_path = target_path.with_name(f"{target_path.name}_to_delete_{timestamp}")
    staging_path = target_path.with_name(f"{target_path.name}_restore_{spec.snap_id}_{timestamp}")
    return PreparedRestore(spec, source_snapshot, target_path, temp_delete_path, staging_path)

def ensure_no_nested_subvolumes(plan: PreparedRestore) -> None:
    result = run_cmd(["btrfs", "subvolume", "list", "-o", str(plan.target_path)], check=False)
    if result.returncode != 0:
        fail(f"[!] Fatal: Failed to inspect nested subvolumes inside '{plan.spec.active_subvol}' for config '{plan.spec.config}'.\n{error_text(result)}")

    nested_output = result.stdout.strip()
    if nested_output:
        fail(
            f"\n[!] CRITICAL HALT: Nested subvolumes detected physically inside '{plan.spec.active_subvol}' for config '{plan.spec.config}'!\n\n"
            f"Offending subvolumes:\n{nested_output}\n\n"
            f"[!] An atomic rollback would trap these inside the subvolume slated for deletion.\n"
            f"[!] Please check what these are. You may need to flatten your Btrfs topology (e.g., move Docker to a separate top-level subvolume)."
        )

def rollback_prepared_restores(plans: list[PreparedRestore], original_exc: Exception) -> None:
    rollback_errors: list[str] = []
    for plan in reversed(plans):
        if plan.activated and plan.target_path.exists() and not plan.staging_path.exists():
            try: plan.target_path.rename(plan.staging_path)
            except OSError as exc: rollback_errors.append(f"{plan.spec.config}: failed to move restored subvolume out of the way: {exc}")

    for plan in reversed(plans):
        if plan.active_moved and plan.temp_delete_path.exists() and not plan.target_path.exists():
            try: plan.temp_delete_path.rename(plan.target_path)
            except OSError as exc: rollback_errors.append(f"{plan.spec.config}: failed to restore original active subvolume: {exc}")

    for plan in reversed(plans):
        if plan.staging_path.exists():
            result = run_cmd(["btrfs", "subvolume", "delete", str(plan.staging_path)], check=False)
            if result.returncode != 0:
                rollback_errors.append(f"{plan.spec.config}: failed to delete staging subvolume '{plan.staging_path.name}': {error_text(result)}")

    if rollback_errors:
        joined = "\n".join(f"- {item}" for item in rollback_errors)
        fail(f"[!] Fatal: Restore failed and rollback was incomplete.\n{original_exc}\n{joined}")
    fail(f"[!] Fatal: Restore failed. Rolled back successfully.\n{original_exc}")

def apply_prepared_restores(plans: list[PreparedRestore]) -> None:
    seen_targets: set[str] = set()

    for plan in plans:
        target_key = str(plan.target_path)
        if target_key in seen_targets: fail(f"[!] Fatal: Multiple restore targets resolve to the same path: {target_key}")
        seen_targets.add(target_key)

        if not plan.source_snapshot.is_dir(): fail(f"[!] Fatal: Snapshot ID {plan.spec.snap_id} does not exist at {plan.source_snapshot}")
        if not plan.target_path.is_dir(): fail(f"[!] Fatal: Active subvolume path does not exist for config '{plan.spec.config}': {plan.target_path}")
        if plan.temp_delete_path.exists(): fail(f"[!] Fatal: Deletion path already exists for config '{plan.spec.config}': {plan.temp_delete_path}")
        if plan.staging_path.exists(): fail(f"[!] Fatal: Staging path already exists for config '{plan.spec.config}': {plan.staging_path}")
        ensure_no_nested_subvolumes(plan)

    try:
        for plan in plans:
            print(f"\033[1;38;5;81m[*] Creating staged restore subvolume for '{plan.spec.config}': {plan.staging_path.name}...\033[0m")
            run_cmd_raise(["btrfs", "subvolume", "snapshot", str(plan.source_snapshot), str(plan.staging_path)])
            plan.staging_created = True

        for plan in plans:
            print(f"\033[1;38;5;81m[*] Unlinking current active subvolume for '{plan.spec.config}'...\033[0m")
            plan.target_path.rename(plan.temp_delete_path)
            plan.active_moved = True

        for plan in plans:
            print(f"\033[1;38;5;81m[*] Activating restored snapshot for '{plan.spec.config}' as {plan.target_path.name}...\033[0m")
            plan.staging_path.rename(plan.target_path)
            plan.activated = True
            
        for plan in plans:
            print(f"\033[1;38;5;81m[*] Permanently deleting previous system state for '{plan.spec.config}'...\033[0m")
            deleted = False
            for attempt in range(3):
                del_res = run_cmd(["btrfs", "subvolume", "delete", str(plan.temp_delete_path)], check=False)
                if del_res.returncode == 0:
                    deleted = True
                    break
                time.sleep(1)

            if not deleted:
                print(f"\033[1;38;5;220m[!] Warning: Immediate deletion failed. Scheduling aggressive background cleanup on next boot...\033[0m", file=sys.stderr)
                try:
                    uuid_res = run_cmd(["findmnt", "-n", "-e", "-o", "UUID", "-M", plan.spec.target_mnt], check=False)
                    uuid = uuid_res.stdout.strip()
                    if not uuid or uuid == "-":
                        device_res = run_cmd(["findmnt", "-n", "-v", "-e", "-o", "SOURCE", "-M", plan.spec.target_mnt], check=False)
                        device = device_res.stdout.strip()
                        if device.startswith("/dev/"):
                            blkid_res = run_cmd(["blkid", "-s", "UUID", "-o", "value", device], check=False)
                            uuid = blkid_res.stdout.strip()

                    if not uuid:
                        print(f"\033[1;38;5;196m[!] Error: Could not determine UUID for {plan.spec.target_mnt}. Manual deletion required.\033[0m", file=sys.stderr)
                        continue

                    subvol_name = plan.temp_delete_path.name
                    service_name = f"dusky-cleanup-{subvol_name}.service"
                    service_path = Path("/etc/systemd/system") / service_name
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
                    print(f"\033[1;38;5;196m[!] Failed to schedule boot cleanup: {e}\n[!] Manual deletion required.\033[0m", file=sys.stderr)

    except (OSError, RuntimeError) as exc:
        rollback_prepared_restores(plans, exc)


# =============================================================================
# JSON PARSING, SNAPPER CLI EXTRACTION & COORDINATION ALGORITHMS
# =============================================================================

def first_present(mapping: dict[str, object], *keys: str) -> object | None:
    for key in keys:
        if key in mapping and mapping[key] is not None: return mapping[key]
    return None

def normalize_json_key(value: str) -> str:
    raw = value.strip()
    if raw == "#": return "number"
    normalized = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    aliases = {"num": "number", "id": "id", "snapshot_id": "id", "type": "type", "snapshot_type": "snapshot_type", "date": "date", "timestamp": "timestamp", "time": "time", "description": "description", "desc": "description"}
    return aliases.get(normalized, normalized)

def looks_like_snapshot_record(obj: object) -> bool:
    if not isinstance(obj, dict): return False
    id_value = first_present(obj, "number", "id", "num", "#")
    aux_value = first_present(obj, "date", "timestamp", "time", "description", "desc", "type", "snapshot_type")
    return id_value is not None and aux_value is not None

def find_snapshot_records(obj: object) -> list[dict[str, object]] | None:
    if isinstance(obj, list):
        if obj and all(isinstance(item, dict) for item in obj) and any(looks_like_snapshot_record(item) for item in obj): return list(obj)
        for item in obj:
            found = find_snapshot_records(item)
            if found is not None: return found
        return None
    if isinstance(obj, dict):
        for key in ("snapshots", "entries", "data", "list"):
            if key in obj:
                found = find_snapshot_records(obj[key])
                if found is not None: return found
        for value in obj.values():
            found = find_snapshot_records(value)
            if found is not None: return found
    return None

def find_tabular_snapshot_records(obj: object) -> list[dict[str, object]] | None:
    if isinstance(obj, dict):
        columns = obj.get("columns")
        rows = obj.get("rows")
        if rows is None: rows = obj.get("data")
        if isinstance(columns, list) and isinstance(rows, list):
            column_names: list[str] = []
            for column in columns:
                if isinstance(column, str): column_names.append(normalize_json_key(column))
                elif isinstance(column, dict):
                    label = None
                    for candidate in ("name", "key", "id", "title", "label"):
                        if candidate in column and column[candidate] is not None:
                            label = str(column[candidate])
                            break
                    column_names.append(normalize_json_key("" if label is None else label))
                else: column_names.append("")
            if rows and all(isinstance(row, dict) for row in rows):
                candidate_rows = [dict(row) for row in rows]
                if any(looks_like_snapshot_record(row) for row in candidate_rows): return candidate_rows
            if rows and all(isinstance(row, (list, tuple)) for row in rows):
                records: list[dict[str, object]] = []
                for row in rows:
                    record: dict[str, object] = {}
                    for index, value in enumerate(row):
                        key = column_names[index] if index < len(column_names) and column_names[index] else f"col_{index}"
                        record[key] = value
                    records.append(record)
                if records and any(looks_like_snapshot_record(record) for record in records): return records
        for value in obj.values():
            found = find_tabular_snapshot_records(value)
            if found is not None: return found
    elif isinstance(obj, list):
        for item in obj:
            found = find_tabular_snapshot_records(item)
            if found is not None: return found
    return None

def extract_snapshot_records(payload: object) -> list[dict[str, object]] | None:
    records = find_snapshot_records(payload)
    if records is not None: return records
    return find_tabular_snapshot_records(payload)

def parse_snapshot_datetime(raw_value: object) -> datetime | None:
    if raw_value is None: return None
    if isinstance(raw_value, int | float):
        try: return datetime.fromtimestamp(raw_value)
        except (OverflowError, OSError, ValueError): return None
    raw = str(raw_value).strip()
    if not raw: return None
    iso_candidates = [raw]
    if " " in raw: iso_candidates.append(raw.replace(" ", "T", 1))
    for candidate in iso_candidates:
        try: return datetime.fromisoformat(candidate)
        except ValueError: pass
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try: return datetime.strptime(raw, pattern)
        except ValueError: continue
    tokens = raw.split()
    if len(tokens) >= 7 and tokens[-1].isalpha():
        try:
            clean_date = " ".join(tokens[:-1])
            return datetime.strptime(clean_date, "%a %d %b %Y %I:%M:%S %p")
        except ValueError: pass
    return None

def format_snapshot_date(raw_value: object) -> str:
    dt = parse_snapshot_datetime(raw_value)
    if dt is not None: return dt.strftime("%m/%d/%y %I:%M %p")
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
        if snap_id_value is None: continue
        
        snap_id = re.sub(r'[*+-]+$', '', str(snap_id_value).strip())
        if snap_id == "0" or not snap_id.isdigit(): continue

        raw_date_value = first_present(record, "date", "timestamp", "time")
        raw_date = "" if raw_date_value is None else str(raw_date_value)
        dt = parse_snapshot_datetime(raw_date_value)
        
        pre_num = str(first_present(record, "pre_number", "pre_num") or "").strip()
        if pre_num == "0": pre_num = ""

        gui_data.append({
            "id": snap_id,
            "type": str(first_present(record, "type", "snapshot_type") or ""),
            "date": format_snapshot_date(raw_date_value),
            "raw_date": raw_date,
            "description": str(first_present(record, "description", "desc") or ""),
            "cleanup": str(first_present(record, "cleanup", "cleanup_algorithm") or ""),
            "userdata": str(first_present(record, "userdata", "user_data") or ""),
            "user": str(first_present(record, "user", "creator") or "root"),
            "pre_number": pre_num,
            "age": time_ago(dt) if dt else "Unknown"
        })
    return gui_data

def parse_snapper_table(stdout: str) -> list[dict[str, str]]:
    gui_data: list[dict[str, str]] = []
    for line in stdout.splitlines():
        if not line.strip(): continue
        parts = [part.strip() for part in re.split(r"[|│]", line)]
        if len(parts) < 7: continue

        snap_id = re.sub(r'[*+-]+$', '', parts[0])
        if snap_id == "0" or not snap_id.isdigit(): continue

        raw_date = parts[3]
        dt = parse_snapshot_datetime(raw_date)
        
        gui_data.append({
            "id": snap_id,
            "type": parts[1],
            "date": format_snapshot_date(raw_date),
            "raw_date": raw_date,
            "description": parts[6] if len(parts) > 6 else "",
            "cleanup": parts[5] if len(parts) > 5 else "",
            "userdata": "|".join(parts[7:]).strip() if len(parts) > 7 else "",
            "user": parts[4] if len(parts) > 4 else "root",
            "pre_number": parts[2] if parts[2] != "-" else "",
            "age": time_ago(dt) if dt else "Unknown"
        })
    return gui_data

def load_snapshot_list_for_gui(config: str) -> list[dict[str, str]]:
    result = run_cmd(["snapper", "--jsonout", "-c", config, "list", "--disable-used-space"], check=False)
    if result.returncode != 0: return []
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        result = run_cmd(["snapper", "-c", config, "list", "--disable-used-space"], check=False)
        return parse_snapper_table(result.stdout) if result.returncode == 0 else []

    records = extract_snapshot_records(payload)
    if records is None:
        result = run_cmd(["snapper", "-c", config, "list", "--disable-used-space"], check=False)
        return parse_snapper_table(result.stdout) if result.returncode == 0 else []

    return snapshot_records_to_gui(records)

def load_all_snapper_data() -> list[dict[str, str]]:
    configs = get_snapper_configs()
    all_snaps = []
    for cfg_info in configs:
        cfg = cfg_info["config"]
        base_path = cfg_info["subvolume"]
        snaps_mnt = "/.snapshots" if base_path == "/" else f"{base_path.rstrip('/')}/.snapshots"
        gui_snaps = load_snapshot_list_for_gui(cfg)
        for s in gui_snaps:
            s["config"] = cfg
            s["location"] = f"{snaps_mnt}/{s['id']}/snapshot"
            all_snaps.append(s)
    return all_snaps

def find_coordinated_pair(target_date: str, target_desc: str | None = None) -> tuple[str, str]:
    root_snaps = load_snapshot_list_for_gui("root")
    if not root_snaps: raise RuntimeError("[!] Fatal: Failed to query Root snapshots or list is empty.")
    home_snaps = load_snapshot_list_for_gui("home")
    if not home_snaps: raise RuntimeError("[!] Fatal: Failed to query Home snapshots or list is empty.")

    exact_root = [s["id"] for s in root_snaps if s.get("raw_date") == target_date]
    if len(exact_root) == 1: root_id = exact_root[0]
    elif not exact_root: raise RuntimeError(f"[!] Fatal: Could not find Root snapshot for exact date: {target_date}")
    else: raise RuntimeError(f"[!] Fatal: Multiple Root snapshots matched exact date: {target_date}")

    exact_home = [s["id"] for s in home_snaps if s.get("raw_date") == target_date]
    if len(exact_home) == 1: return root_id, exact_home[0]
    if len(exact_home) > 1: raise RuntimeError(f"[!] Fatal: Multiple Home snapshots matched exact date.")

    def minute_prefix(val: str) -> str | None:
        match = re.search(r"^(.*\d{2}:\d{2})", val)
        return match.group(1) if match else None

    if target_desc:
        t_min = minute_prefix(target_date)
        if t_min:
            fuzzy = [s["id"] for s in home_snaps if s.get("description") == target_desc and minute_prefix(s.get("raw_date", "")) == t_min]
            if len(fuzzy) == 1: return root_id, fuzzy[0]
            if len(fuzzy) > 1: raise RuntimeError("[!] Fatal: Multiple Home snapshots matched fuzzy minute+description.")

    target_dt = parse_snapshot_datetime(target_date)
    if not target_dt: raise RuntimeError("[!] Fatal: Date parsing failed for target date. Cannot perform 120s safety fallback.")

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


# =============================================================================
# CLI COMMAND HANDLERS
# =============================================================================

def is_mountpoint(path: str) -> bool:
    return run_cmd(["mountpoint", "-q", "--", path], check=False).returncode == 0

def activate_nonroot_restore(target_mnt: str) -> None:
    if not is_mountpoint(target_mnt):
        print(f"\033[1;38;5;81m[*] {target_mnt} is not mounted. Restored subvolume applied on next boot.\033[0m")
        return
    print(f"\033[1;38;5;81m[*] Attempting to live-remount {target_mnt} to activate restored snapshot...\033[0m")
    
    if run_cmd(["umount", target_mnt], check=False).returncode != 0:
        print(
            f"\n\033[1;38;5;220m[!] Notice: {target_mnt} is currently in use (target is busy).\n"
            f"[!] The restore was successful on disk, but the live filesystem cannot be swapped.\n"
            f"[\033[1;38;5;196m!\033[1;38;5;220m] WARNING: Any changes made to {target_mnt} right now will be lost upon reboot.\n"
            f"[!] Please REBOOT IMMEDIATELY to activate the restored snapshot.\033[0m"
        )
        return
    
    if run_cmd(["mount", target_mnt], check=False).returncode != 0:
        fail(f"[!] CRITICAL: Restore completed on disk, but remount of {target_mnt} failed!\n[!] Your {target_mnt} directory is currently unmounted. Please resolve manually before rebooting.")
    
    print(f"\033[1;38;5;114m[+] {target_mnt} successfully remounted live.\033[0m")

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
    if config1 == config2: fail("[!] Fatal: Coordinated restore requires two distinct snapper configs.")
    spec1 = resolve_restore_spec(config1, snap_id1)
    spec2 = resolve_restore_spec(config2, snap_id2)

    devices = {spec1.device, spec2.device}
    if len(devices) != 1: fail("[!] Fatal: Coordinated restore requires both configs to live on the same Btrfs filesystem.")
    if spec1.active_subvol == spec2.active_subvol: fail("[!] Fatal: Coordinated restore configs resolve to the same subvolume path.")

    with mount_top_level(spec1.device) as top_mnt:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        plans = [prepare_restore(spec1, top_mnt, timestamp), prepare_restore(spec2, top_mnt, timestamp)]
        apply_prepared_restores(plans)

    print("\n\033[1;38;5;114m[+] Coordinated restoration complete.\033[0m")
    if spec1.target_mnt == "/" or spec2.target_mnt == "/":
        print("\033[1;38;5;196m[!] ROOT FILESYSTEM MODIFIED. You MUST reboot immediately for changes to take effect.\033[0m")
    else:
        print("\033[1;38;5;220m[!] Restored subvolumes were staged on disk. Reboot to activate.\033[0m")

def handle_delete(config: str, snap_id: str) -> None:
    snap_id = validate_snapshot_id(snap_id)
    if snap_id == "0": fail(f"[!] Fatal: Cannot delete snapshot ID 0 (the active system state) for config '{config}'.")
    print(f"\033[1;38;5;81m[*] Deleting snapshot ID {snap_id} for '{config}'...\033[0m")
    run_cmd(["snapper", "-c", config, "delete", snap_id])
    print(f"\033[1;38;5;114m[+] Snapshot ID {snap_id} deleted successfully.\033[0m")

def handle_delete_pair(config1: str, snap_id1: str, config2: str, snap_id2: str) -> None:
    if config1 == config2:
        fail("[!] Fatal: Coordinated deletion requires two distinct snapper configs.")
    handle_delete(config1, snap_id1)
    handle_delete(config2, snap_id2)
    print("\n\033[1;38;5;114m[+] Coordinated deletion complete.\033[0m")


# =============================================================================
# FZF TUI INTEGRATION & RENDERING ENGINE
# =============================================================================

def strip_ansi(text: str) -> str:
    return re.sub(r'\x1b\[[0-9;]*m', '', text)

def draw_tui_panel(title: str, lines: list[str], width: int = 48) -> None:
    title_clean = strip_ansi(title)
    dash_count = max(0, width - len(title_clean) - 5)
    print(f"\033[1;38;5;220m╭─ {title} \033[1;38;5;220m{'─' * dash_count}╮\033[0m")
    for line in lines:
        line_clean = strip_ansi(line)
        pad = max(0, width - len(line_clean) - 4)
        print(f"\033[1;38;5;220m│\033[0m {line}{' ' * pad} \033[1;38;5;220m│\033[0m")
    print(f"\033[1;38;5;220m╰{'─' * (width - 2)}╯\033[0m\n")

def handle_tui_preview(view: str, line: str, show_diff: bool = False) -> None:
    try:
        parts = line.split('\x1f')
        try:
            meta = json.loads(parts[1]) if len(parts) > 1 else {}
        except ValueError:
            meta = {}
            
        if meta.get("empty"):
            print("\033[1;38;5;196m[!] No items available in this view.\033[0m")
            return

        is_subvol = (view == "subvolumes")
        snap_id = meta.get("id", "N/A")
        snap_config = meta.get("config", "root" if view in ("root", "coordinated") else "home")
        
        # 1. Unified Shortcuts Panel
        shortcuts = []
        if is_subvol:
            shortcuts.extend([
                "\033[1;38;5;114m[CTRL-N]\033[0m  \033[38;5;253m󰐕 Create Subvol (NOCOW)\033[0m",
                "\033[1;38;5;81m[CTRL-S]\033[0m  \033[38;5;253m󰎈 Create Native Snapshot\033[0m",
                "\033[1;38;5;213m[CTRL-G]\033[0m  \033[38;5;253m󰒓 Init Snapper Config\033[0m",
                "\033[1;38;5;220m[CTRL-B]\033[0m  \033[38;5;253m󰆗 Backup to Ext. Drive\033[0m",
                "\033[1;38;5;196m[DEL]\033[0m     \033[38;5;253m󰆴 Delete Subvolume\033[0m",
                "\033[1;38;5;246m[TAB]\033[0m     \033[38;5;253m󰓡 Switch View\033[0m"
            ])
            draw_tui_panel("\033[1;38;5;213m󰏖 SUBVOLUME SHORTCUTS\033[0m", shortcuts, 48)
        else:
            shortcuts.extend([
                "\033[1;38;5;114m[ENTER]\033[0m   \033[38;5;253m󰁯 Restore Selected\033[0m",
                "\033[1;38;5;196m[DEL]\033[0m     \033[38;5;253m󰆴 Delete Selected\033[0m",
                "\033[1;38;5;81m[CTRL-S]\033[0m  \033[38;5;253m󰎈 Create New Snapshot\033[0m",
                "\033[1;38;5;220m[CTRL-B]\033[0m  \033[38;5;253m󰆗 Backup to Ext. Drive\033[0m",
                "\033[1;38;5;213m[TAB]\033[0m     \033[38;5;253m󰓡 Switch View\033[0m",
                "\033[1;38;5;246m[CTRL-A/X]\033[0m\033[38;5;253m󰒉 Select/Deselect All\033[0m",
                "\033[1;38;5;246m[CTRL-V/P]\033[0m\033[38;5;253m󰏫 Toggle Diff Mode\033[0m",
                "\033[1;38;5;141m[ALT-P]\033[0m   \033[38;5;253m󰈈 Toggle Preview Pane\033[0m"
            ])
            draw_tui_panel("\033[1;38;5;220m󰏖 KEYBOARD SHORTCUTS\033[0m", shortcuts, 48)

        # 2. Expanded Metadata View
        if is_subvol:
            status = "\033[1;38;5;196mRead-Only\033[0m" if meta.get("is_ro") else "\033[1;38;5;114mRead-Write\033[0m"
            print(f"\033[1;38;5;213m󰋊 SUBVOLUME METADATA\033[0m")
            print(f"\033[38;5;238m" + "─" * 48 + "\033[0m")
            print(f" \033[1;38;5;246mID     \033[0m │ \033[1;38;5;39m{snap_id}\033[0m")
            print(f" \033[1;38;5;246mStatus \033[0m │ {status}")
            print(f" \033[1;38;5;246mDevice \033[0m │ \033[38;5;220m{meta.get('device', 'N/A')}\033[0m")
            print(f" \033[1;38;5;246mTarget \033[0m │ \033[38;5;253m{meta.get('path', 'N/A')}\033[0m")
        else:
            print(f"\033[1;38;5;81m󰆑 SNAPSHOT DETAILS\033[0m")
            print(f"\033[38;5;238m" + "─" * 48 + "\033[0m")
            print(f" \033[1;38;5;246mConfig \033[0m │ \033[1;38;5;253m{snap_config.upper()}\033[0m")
            print(f" \033[1;38;5;246mID     \033[0m │ \033[1;38;5;39m{snap_id}\033[0m")
            print(f" \033[1;38;5;246mType   \033[0m │ \033[38;5;213m{meta.get('type', 'N/A')}\033[0m")
            
            pre = meta.get('pre_number')
            if pre: print(f" \033[1;38;5;246mPre-ID \033[0m │ \033[38;5;216m{pre}\033[0m")
            print(f" \033[1;38;5;246mDate   \033[0m │ \033[38;5;220m{meta.get('date', 'N/A')}\033[0m")
            
            age = meta.get('age')
            if age and age != "Unknown": print(f" \033[1;38;5;246mAge    \033[0m │ \033[38;5;114m{age}\033[0m")
            print(f" \033[1;38;5;246mUser   \033[0m │ \033[38;5;114m{meta.get('user', 'root')}\033[0m")
            
            cln = meta.get('cleanup')
            if cln and cln.lower() != "none": print(f" \033[1;38;5;246mCleanup\033[0m │ \033[38;5;216m{cln}\033[0m")
            
            loc = meta.get('location')
            if loc: print(f" \033[1;38;5;246mPath   \033[0m │ \033[38;5;114m{loc}\033[0m")
            
            print(f" \033[1;38;5;246mDesc   \033[0m │ \033[38;5;253m{meta.get('description', meta.get('desc', 'N/A'))}\033[0m\n")

        # 3. Dynamic Diff Generation
        if show_diff:
            if is_subvol:
                print(f"\033[1;38;5;246m[!] Diff mode not applicable for raw subvolumes.\033[0m")
            else:
                print(f"\033[1;38;5;114m󰏫 FILES CHANGED IF RESTORED\033[0m \033[3;38;5;246m(vs Current System)\033[0m")
                print(f"\033[38;5;238m" + "─" * 48 + "\033[0m")

                def run_diff(cfg: str, s_id: str):
                    print(f"\033[1;38;5;203m▶ System Profile: {cfg}\033[0m")
                    try:
                        result = subprocess.run(["snapper", "-c", cfg, "status", f"{s_id}..0"], capture_output=True, text=True)
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
                            if not l.strip(): continue
                            status, filepath = l[0], l[6:].strip() if len(l) > 6 else l[1:].strip()
                            if status == '+': print(f"  \033[1;38;5;196m[-]\033[0m \033[38;5;246m{filepath}\033[0m")
                            elif status == '-': print(f"  \033[1;38;5;114m[+]\033[0m \033[38;5;253m{filepath}\033[0m")
                            elif status == 'c': print(f"  \033[1;38;5;220m[~]\033[0m \033[38;5;253m{filepath}\033[0m")
                            else: print(f"  \033[38;5;246m{l}\033[0m")
                    except Exception as e:
                        print(f"  \033[38;5;196mExecution Failed: {e}\033[0m")

                if view in ("home", "root", "global"):
                    run_diff(snap_config, snap_id)
                elif view == "coordinated":
                    run_diff("root", snap_id)
                    print()
                    try:
                        _, h_id = find_coordinated_pair(meta.get("raw_date", ""), meta.get("description", ""))
                        run_diff("home", h_id)
                    except RuntimeError:
                        print(f"\033[1;38;5;203m▶ System Profile: home\033[0m\n  \033[3;38;5;196mFailed to locate paired snapshot.\033[0m")
        else:
            print(f"\033[1;38;5;246m[!] File changes hidden for performance.\033[0m")
            print(f"\033[1;38;5;246mPress \033[1;38;5;220m<Ctrl+V>\033[1;38;5;246m to generate file change list.\033[0m")
            print(f"\033[1;38;5;246mPress \033[1;38;5;220m<Ctrl+P>\033[1;38;5;246m to hide and restore fast scrolling.\033[0m")

    except Exception as e:
        print(f"\033[1;38;5;196mError rendering preview:\n{e}\033[0m")

def launch_tui() -> None:
    if not shutil.which("fzf"): fail("[!] Fatal: 'fzf' is required. Install via: pacman -S fzf")

    views = ["home", "root", "coordinated", "global", "subvolumes"]
    view_idx = 0
    
    fzf_colors = (
        "bg+:#1e1e2e,bg:#11111b,spinner:#f5e0dc,fg:#cdd6f4,fg+:#cdd6f4,"
        "header:#89b4fa,info:#cba6f7,pointer:#f5e0dc,marker:#a6e3a1,"
        "prompt:#cba6f7,hl:#f38ba8,hl+:#f38ba8,border:#585b70,label:#a6e3a1"
    )

    executable = shlex.quote(sys.executable)
    script_path = shlex.quote(os.path.abspath(sys.argv[0]))

    while True:
        current_view = views[view_idx]
        lines_for_fzf = []
        c_sep = "\033[38;5;238m│\033[0m"
        hr = "\033[38;5;238m" + "─" * 500 + "\033[0m"

        gb = 1024**3
        total, used, free = shutil.disk_usage("/")
        storage_hdr = f" \033[1;38;5;81m󰋊 BTRFS STORAGE:\033[0m \033[38;5;253m{total/gb:.1f} GB Total\033[0m \033[38;5;238m|\033[0m \033[38;5;203m{used/gb:.1f} GB Used\033[0m \033[38;5;238m|\033[0m \033[38;5;114m{free/gb:.1f} GB Free\033[0m "

        tab_defs = [
            ("home", "󰋜 HOME", "114"),
            ("root", "󰒋 ROOT", "39"),
            ("coordinated", "󰑐 ROOT+HOME", "213"),
            ("global", "󰆑 GLOBAL", "81"),
            ("subvolumes", "󰋊 SUBVOLUMES", "203")
        ]

        tab_strs = []
        for v_id, label, color in tab_defs:
            if v_id == current_view: tab_strs.append(f"\033[1;38;5;232;48;5;{color}m {label} \033[0m")
            else: tab_strs.append(f"\033[38;5;246m {label} \033[0m")
        mode_hdr = "  " + "  ".join(tab_strs)

        if current_view == "subvolumes":
            table_hdr = f"\033[1;38;5;242m{'ID':>4}\033[0m {c_sep} \033[1;38;5;242m{'DEVICE HINT':<15}\033[0m {c_sep} \033[1;38;5;242mBTRFS PATH\033[0m"
            lines_for_fzf.extend([mode_hdr, hr, table_hdr])
            subvols = get_all_subvolumes()
            if subvols:
                for sv in sorted(subvols, key=lambda x: x['path']):
                    id_str = f"\033[1;38;5;39m{sv['id']:>4}\033[0m"
                    mnt_str = f"\033[38;5;220m{sv['mount_target']:<15}\033[0m"
                    path_str = f"\033[38;5;253m{sv['path']}\033[0m"
                    vis = f"{id_str} {c_sep} {mnt_str} {c_sep} {path_str}"
                    lines_for_fzf.append(f"{vis}\x1f{json.dumps(sv)}")
            else:
                dummy_vis = f"\033[38;5;246m{'N/A':>4}\033[0m {c_sep} {'No subvolumes':<15} {c_sep}"
                lines_for_fzf.append(f"{dummy_vis}\x1f{json.dumps({'empty': True})}")

        elif current_view == "global":
            table_hdr = f"\033[1;38;5;242m{'CFG':<8}\033[0m {c_sep} \033[1;38;5;242m{'ID':>4}\033[0m {c_sep} \033[1;38;5;242m{'AGE':<10}\033[0m {c_sep} \033[1;38;5;242m{'DATE':<18}\033[0m {c_sep} \033[1;38;5;242mDESCRIPTION\033[0m"
            lines_for_fzf.extend([mode_hdr, hr, table_hdr])
            snaps = load_all_snapper_data()
            if snaps:
                for s in sorted(snaps, key=lambda x: (x['config'], int(x.get('number', x.get('id', 0)))), reverse=True):
                    cfg_str = f"\033[38;5;213m{s['config']:<8}\033[0m"
                    id_str = f"\033[1;38;5;39m{s.get('id', '0'):>4}\033[0m"
                    age_str = f"\033[38;5;114m{s.get('age', ''):<10}\033[0m"
                    date_str = f"\033[38;5;220m{s.get('date', ''):<18}\033[0m"
                    desc_str = f"\033[38;5;253m{s.get('description', '')}\033[0m"
                    vis = f"{cfg_str} {c_sep} {id_str} {c_sep} {age_str} {c_sep} {date_str} {c_sep} {desc_str}"
                    
                    meta = {
                        "config": s['config'],
                        "id": s.get('id'),
                        "date": date_str.strip(),
                        "raw_date": s.get('raw_date', ''),
                        "desc": desc_str.strip(),
                        "location": s.get('location'),
                        "age": s.get('age', ''),
                        "user": s.get('user', ''),
                        "cleanup": s.get('cleanup', '')
                    }
                    lines_for_fzf.append(f"{vis}\x1f{json.dumps(meta)}")
            else:
                dummy_vis = f"\033[38;5;246m{'Empty':<8}\033[0m {c_sep} {'':>4} {c_sep} {'':<10} {c_sep} {'No snapshots':<18} {c_sep}"
                lines_for_fzf.append(f"{dummy_vis}\x1f{json.dumps({'empty': True})}")

        else:
            hdr_id = f"\033[1;38;5;242m{'ID':>4}\033[0m"
            hdr_type = f"\033[1;38;5;242m{'TYPE':<7}\033[0m"
            hdr_age = f"\033[1;38;5;242m{'AGE':<10}\033[0m"
            hdr_date = f"\033[1;38;5;242m{'DATE':<18}\033[0m"
            hdr_desc = f"\033[1;38;5;242mDESCRIPTION\033[0m"
            table_hdr = f"{hdr_id} {c_sep} {hdr_type} {c_sep} {hdr_age} {c_sep} {hdr_date} {c_sep} {hdr_desc}"
            lines_for_fzf.extend([mode_hdr, hr, table_hdr])
            
            config_to_query = "root" if current_view in ("coordinated", "root") else "home"
            snaps = load_snapshot_list_for_gui(config_to_query)
            
            if snaps:
                snap_dir = get_target_mount_from_snapper_config(config_to_query)
                snaps_mnt = "/.snapshots" if snap_dir == "/" else f"{snap_dir.rstrip('/')}/.snapshots"
                for s in sorted(snaps, key=lambda x: int(x["id"]), reverse=True):
                    id_str = f"\033[1;38;5;39m{s['id']:>4}\033[0m"         
                    type_str = f"\033[38;5;213m{s['type']:<7}\033[0m"       
                    age_colored = f"\033[38;5;114m{s.get('age', ''):<10}\033[0m"    
                    date_str = f"\033[38;5;220m{s['date']:<18}\033[0m"     
                    desc_str = f"\033[38;5;253m{s.get('description', '')}\033[0m"  
                    vis = f"{id_str} {c_sep} {type_str} {c_sep} {age_colored} {c_sep} {date_str} {c_sep} {desc_str}"
                    s["config"] = config_to_query
                    s["location"] = f"{snaps_mnt}/{s['id']}/snapshot"
                    lines_for_fzf.append(f"{vis}\x1f{json.dumps(s)}")
            else:
                lines_for_fzf.append(f"\033[1;38;5;196m No snaps found.\033[0m\x1f{{\"empty\": true}}")

        preview_cmd = f"{executable} {script_path} --tui-preview {current_view} {{}}"
        preview_diff_cmd = f"{executable} {script_path} --tui-preview {current_view} --show-diff {{}}"
        transform_cmd = 'echo "print(click-header:$FZF_CLICK_HEADER_LINE:$FZF_CLICK_HEADER_COLUMN)+accept"'
        click_bind = f"click-header:transform[{transform_cmd}]"

        fzf_cmd = [
            "fzf", "--multi", "--ansi", "--reverse", "--delimiter=\\x1f", "--with-nth=1",
            "--header", storage_hdr, "--header-first", "--header-lines=3", 
            "--border=rounded", "--border-label", " Dusky Snapshots ",
            "--prompt= :: Action ❯ ", f"--color={fzf_colors}", "--pointer=▌", "--marker=▶",
            "--no-hscroll", "--ellipsis=",
            "--expect=enter,ctrl-d,delete,tab,ctrl-s,alt-s,ctrl-n,ctrl-g,ctrl-b",
            f"--bind={click_bind},ctrl-a:select-all,ctrl-x:deselect-all,ctrl-space:toggle,shift-down:toggle+down,shift-up:toggle+up,ctrl-p:change-preview({preview_cmd})+change-prompt( :: Action ❯ ),ctrl-v:change-preview({preview_diff_cmd})+change-prompt( :: Diff Mode ON ❯ ),alt-p:toggle-preview",
            "--info=hidden", "--preview", preview_cmd, "--preview-window", "right,45%,border-left,wrap"
        ]

        try:
            process = subprocess.Popen(fzf_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8")
            stdout, _ = process.communicate(input="\n".join(lines_for_fzf))
        except Exception as exc:
            fail(f"[!] FZF Execution failed: {exc}")

        if process.returncode in (130, 2):
            print("\n\033[1;38;5;196m[!] Terminated by user.\033[0m", file=sys.stderr)
            sys.exit(130)

        if not stdout.strip(): break

        output_lines = stdout.strip().split("\n")
        key_pressed = output_lines[0]

        # Mouse click tab routing logic matching header layout hitboxes
        clicked_tab = False
        for out_line in output_lines:
            if out_line.startswith("click-header:"):
                parts = out_line.split(":")
                if len(parts) >= 3:
                    line = int(parts[1]) if parts[1].isdigit() else 0
                    col = int(parts[2]) if parts[2].isdigit() else 0
                    if line in (1, 2, 3):
                        if col <= 11: view_idx = 0
                        elif col <= 21: view_idx = 1
                        elif col <= 36: view_idx = 2
                        elif col <= 48: view_idx = 3
                        else: view_idx = 4
                        clicked_tab = True
                break
        
        if clicked_tab: continue
        if key_pressed == "tab":
            view_idx = (view_idx + 1) % len(views)
            continue

        selected_metas = []
        for line in output_lines[1:]:
            parts = line.split('\x1f')
            if len(parts) > 1:
                try:
                    meta = json.loads(parts[1])
                    if not meta.get("empty") and "id" in meta: 
                        selected_metas.append(meta)
                except ValueError: pass

        # Global Actions Processing
        if key_pressed in ("ctrl-s", "alt-s") and current_view != "subvolumes":
            print(f"\n\033[1;38;5;81m[*] Action: CREATE NEW SNAPSHOT\033[0m")
            try:
                target_cfg = "root" if current_view == "coordinated" else current_view
                if current_view == "global":
                    target_cfg = input("\033[1;38;5;220m[*] Target Config (e.g., root, home): \033[0m").strip()
                if target_cfg:
                    desc = input("\033[1;38;5;220m[*] Snapshot Description: \033[0m").strip()
                    if desc:
                        if current_view == "coordinated": handle_create_pair("root", "home", desc)
                        else: handle_create(target_cfg, desc)
            except KeyboardInterrupt: pass
            input("\n\033[1;38;5;114mPress Enter to return...\033[0m")
            continue

        if not selected_metas: continue
        meta = selected_metas[0]

        # View-Specific Handlers
        if current_view == "coordinated":
            pairs_to_process = []
            has_error = False
            for m_data in selected_metas:
                sid = str(m_data["id"])
                print(f"\n\033[1;38;5;81m[*] Synchronizing snapshots for Root ID {sid}...\033[0m")
                try:
                    r_id, h_id = find_coordinated_pair(m_data.get("raw_date", ""), m_data.get("description", ""))
                    pairs_to_process.append((r_id, h_id))
                except RuntimeError as e:
                    print(f"\033[1;38;5;196m{e}\033[0m")
                    has_error = True
            
            if has_error:
                input("\n\033[1;38;5;114mPress Enter to return...\033[0m")
                continue

            if key_pressed == "enter":
                if len(pairs_to_process) > 1:
                    print("\n\033[1;38;5;196m[!] Error: Please select only one pair to restore.\033[0m")
                    input("\033[1;38;5;114mPress Enter to return...\033[0m")
                    continue
                else:
                    r_id, h_id = pairs_to_process[0]
                    print(f"\n\033[1;38;5;81m[*] Action: COORDINATED RESTORE\033[0m\n[*] Target Pair : Root={r_id} | Home={h_id}")
                    if confirm_prompt("Are you absolutely sure you want to RESTORE your system to this state?"):
                        handle_restore_pair("root", r_id, "home", h_id)
                        break
            elif key_pressed in ("ctrl-d", "delete"):
                print(f"\n\033[1;38;5;196m[*] Action: COORDINATED DELETE ({len(pairs_to_process)} pairs)\033[0m")
                for r_id, h_id in pairs_to_process:
                    print(f"[*] Target Pair : Root={r_id} | Home={h_id}")
                if confirm_prompt("Permanently delete these snapshot pair(s)?"):
                    for r_id, h_id in pairs_to_process:
                        handle_delete_pair("root", r_id, "home", h_id)
            elif key_pressed == "ctrl-b":
                print("\n\033[1;38;5;196m[!] Backup is not supported in Coordinated mode. Switch to Global or Subvolumes tab.\033[0m")
                input("\n\033[1;38;5;114mPress Enter to return...\033[0m")
                
        elif current_view in ("home", "root", "global"):
            if key_pressed == "enter":
                if len(selected_metas) > 1:
                    print("\n\033[1;38;5;196m[!] Error: Please select only one snapshot to restore.\033[0m")
                    input("\033[1;38;5;114mPress Enter to return...\033[0m")
                    continue
                else:
                    print(f"\n\033[1;38;5;81m[*] Action: RESTORE (Config: {meta['config']} | ID: {meta['id']})\033[0m")
                    if confirm_prompt("Are you absolutely sure you want to RESTORE?"):
                        handle_restore(meta["config"], str(meta["id"]), False)
                        break
            elif key_pressed in ("ctrl-d", "delete"):
                print(f"\n\033[1;38;5;196m[*] Action: DELETE ({len(selected_metas)} snapshots)\033[0m")
                if confirm_prompt(f"Permanently delete {len(selected_metas)} snapshot(s)?"):
                    for m in selected_metas: handle_delete(m["config"], str(m["id"]))
            elif key_pressed == "ctrl-b":
                if len(selected_metas) > 1:
                    print("\n\033[1;38;5;196m[!] Error: Please select only one snapshot to backup.\033[0m")
                    input("\033[1;38;5;114mPress Enter to return...\033[0m")
                    continue
                loc = meta.get("location")
                if loc:
                    cfg = meta.get("config")
                    target_mnt = get_target_mount_from_snapper_config(cfg)
                    dev = get_btrfs_device(target_mnt)
                    snapshots_mnt = "/.snapshots" if target_mnt == "/" else f"{target_mnt.rstrip('/')}/.snapshots"
                    snapshots_subvol = get_active_subvol(snapshots_mnt)
                    
                    sv_rel = f"{snapshots_subvol.lstrip('/')}/{meta['id']}/snapshot" 
                    
                    print(f"\n\033[1;38;5;213m[*] Action: EXTERNAL BACKUP (Send/Receive)\033[0m\n[*] Source Path: {sv_rel}")
                    try:
                        dest = input("\033[1;38;5;220m[*] Destination Path (e.g., /mnt/ExternalDrive): \033[0m").strip()
                        if dest: backup_snapshot_to_external(dev, sv_rel, dest)
                    except KeyboardInterrupt: pass
                    input("\n\033[1;38;5;114mPress Enter to return...\033[0m")

        elif current_view == "subvolumes":
            if key_pressed in ("ctrl-n", "ctrl-s", "ctrl-g", "ctrl-b") and len(selected_metas) > 1:
                print("\n\033[1;38;5;196m[!] Error: Please select only one subvolume for this action.\033[0m")
                input("\033[1;38;5;114mPress Enter to return...\033[0m")
                continue

            dev = meta['device']
            sv_path = meta['path']
            
            match key_pressed:
                case "ctrl-n":
                    print(f"\n\033[1;38;5;213m[*] ACTION: CREATE NEW SUBVOLUME\033[0m")
                    try:
                        parent = input("\033[1;38;5;220m[*] Parent Directory (e.g., /mnt/data): \033[0m").strip()
                        name = input("\033[1;38;5;220m[*] New Subvolume Name: \033[0m").strip()
                        if parent and name:
                            disable_cow = confirm_prompt("Disable Copy-On-Write (NOCOW / chattr +C)?")
                            create_nocow_subvolume(parent, name, disable_cow)
                    except KeyboardInterrupt: pass
                    input("\n\033[1;38;5;114mPress Enter to return...\033[0m")
                case "ctrl-s":
                    print(f"\n\033[1;38;5;81m[*] ACTION: CREATE NATIVE BTRFS SNAPSHOT\033[0m\n[*] Source: {sv_path}")
                    try:
                        dest_rel = input("\033[1;38;5;220m[*] Destination path (relative to BTRFS root, e.g. @snapshots/new_snap): \033[0m").strip()
                        if dest_rel:
                            is_ro = confirm_prompt("Make snapshot Read-Only?")
                            with mount_top_level(dev) as top_mnt:
                                src_abs = top_mnt / sv_path.lstrip("/")
                                dest_abs = str(top_mnt / dest_rel.lstrip("/"))
                                if not src_abs.exists():
                                    print(f"\033[1;38;5;196m[!] Error: Source subvolume no longer exists physically: {sv_path}\033[0m")
                                    continue
                                cmd = ["btrfs", "subvolume", "snapshot", "-r"] if is_ro else ["btrfs", "subvolume", "snapshot"]
                                run_cmd(cmd + [str(src_abs), dest_abs])
                            print("\033[1;38;5;114m[+] Snapshot created successfully.\033[0m")
                    except KeyboardInterrupt: pass
                    input("\n\033[1;38;5;114mPress Enter to return...\033[0m")
                case "ctrl-g":
                    print(f"\n\033[1;38;5;213m[*] ACTION: INITIALIZE SNAPPER CONFIG\033[0m\n[*] Target: {sv_path}")
                    try:
                        print("\033[1;38;5;220m[!] Note: The target subvolume MUST be mounted live on your system for Snapper to initialize.\033[0m")
                        live_mnt = input("\033[1;38;5;220m[*] Enter its live mount point (e.g., /var/log): \033[0m").strip()
                        cfg_name = input("\033[1;38;5;220m[*] Name for new Snapper config: \033[0m").strip()
                        if live_mnt and cfg_name:
                            run_cmd(["snapper", "-c", cfg_name, "create-config", live_mnt])
                            print(f"\033[1;38;5;114m[+] Snapper configuration '{cfg_name}' initialized.\033[0m")
                    except KeyboardInterrupt: pass
                    input("\n\033[1;38;5;114mPress Enter to return...\033[0m")
                case "ctrl-b":
                    print(f"\n\033[1;38;5;213m[*] ACTION: EXTERNAL BACKUP (Send/Receive)\033[0m\n[*] Source: {sv_path}")
                    try:
                        dest = input("\033[1;38;5;220m[*] Enter Destination Path (e.g., /mnt/ExternalDrive): \033[0m").strip()
                        if dest: backup_snapshot_to_external(dev, sv_path.lstrip("/"), dest)
                    except KeyboardInterrupt: pass
                    input("\n\033[1;38;5;114mPress Enter to return...\033[0m")
                case "delete" | "ctrl-d":
                    print(f"\n\033[1;38;5;196m[*] ACTION: DELETE SUBVOLUME(S) ({len(selected_metas)} selected)\033[0m")
                    
                    # [SURGICAL FIX] SYSTEM GUARDRAIL: Identify protected OS partitions dynamically
                    # Dynamically queries live mountinfo to protect EVERY currently active partition
                    protected_subvols = set()
                    
                    for mnt in get_btrfs_mounts():
                        target = mnt.get("target")
                        options = mnt.get("options", "")
                        match = re.search(r"(?:^|,)subvol=([^,]+)(?:,|$)", options)
                        if match:
                            protected_subvols.add(match.group(1).strip("/"))
                        else:
                            show_cmd = run_cmd(["btrfs", "subvolume", "show", target], check=False)
                            if show_cmd.returncode == 0:
                                show_match = re.search(r"^[ \t]*Path:[ \t]*(.+)$", show_cmd.stdout, re.MULTILINE)
                                if show_match:
                                    path_clean = show_match.group(1).strip().strip("/")
                                    if path_clean and path_clean != "<FS_TREE>":
                                        protected_subvols.add(path_clean)
                    
                    safe_to_delete = []
                    for m in selected_metas: 
                        p_clean = m['path'].strip("/")
                        if p_clean in protected_subvols:
                            print(f"\033[1;38;5;196m[!] SYSTEM GUARDRAIL ACTIVATED: Refusing to delete active system partition -> {m['path']}\033[0m")
                        else:
                            print(f"[*] Target: {m['path']}")
                            safe_to_delete.append(m)
                            
                    if not safe_to_delete:
                        input("\n\033[1;38;5;114mPress Enter to return...\033[0m")
                        continue

                    if confirm_prompt("DANGER: Permanently delete these BTRFS subvolume(s)?"):
                        dev_map = {}
                        for m in safe_to_delete:
                            dev_map.setdefault(m['device'], []).append(m['path'])
                        for dev_key, paths in dev_map.items():
                            with mount_top_level(dev_key) as top_mnt:
                                for p in paths:
                                    # Just-in-time existence verification since we bypassed it during scan
                                    target_abs = top_mnt / p.lstrip("/")
                                    if not target_abs.exists():
                                        print(f"\033[1;38;5;220m[-] Subvolume already deleted or missing: {p}\033[0m")
                                        continue
                                    run_cmd(["btrfs", "subvolume", "delete", str(target_abs)])


# =============================================================================
# ENTRY POINT & ARGUMENT PARSING
# =============================================================================

def main() -> None:
    ensure_root()

    if len(sys.argv) == 1:
        launch_tui()
        sys.exit(0)

    parser = argparse.ArgumentParser(description="Unified BTRFS & Snapper Advanced Manager")
    parser.add_argument("-c", "--config", help="Target Snapper configuration")
    parser.add_argument("--json", action="store_true", help="Format list output as JSON")
    parser.add_argument("--no-remount", action="store_true", help="Skip live remount after non-root restore")
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("-l", "--list", action="store_true", help="List snapshots for the configuration")
    group.add_argument("-C", "--create", metavar="DESC", help="Create new snapshot")
    group.add_argument("-R", "--restore", metavar="ID", help="Restore subvolume to snapshot ID")
    group.add_argument("-D", "--delete", metavar="ID", help="Delete snapshot ID")
    group.add_argument("--restore-pair", nargs=4, metavar=("CFG1", "ID1", "CFG2", "ID2"), help="Coordinated restore")
    group.add_argument("--delete-pair", nargs=4, metavar=("CFG1", "ID1", "CFG2", "ID2"), help="Coordinated delete")
    group.add_argument("--sync-restore", nargs="+", metavar="ARGS", help="Auto match coordinated restore (TARGET_DATE [DESC])")
    group.add_argument("--sync-delete", nargs="+", metavar="ARGS", help="Auto match coordinated deletion (TARGET_DATE [DESC])")

    args = parser.parse_args()
    if (args.list or args.create is not None or args.restore is not None or args.delete is not None) and not args.config:
        parser.error("-c/--config is required with --list, --create, --restore, and --delete")

    if args.list: handle_list(args.config, args.json)
    elif args.create is not None: handle_create(args.config, args.create)
    elif args.restore is not None: handle_restore(args.config, args.restore, args.no_remount)
    elif args.delete is not None: handle_delete(args.config, args.delete)
    elif args.delete_pair is not None: handle_delete_pair(*args.delete_pair)
    elif args.restore_pair is not None: handle_restore_pair(*args.restore_pair)
    elif args.sync_restore is not None:
        if len(args.sync_restore) < 1 or len(args.sync_restore) > 2:
            parser.error("--sync-restore requires 1 or 2 arguments: TARGET_DATE [TARGET_DESC]")
        target_date = args.sync_restore[0]
        target_desc = args.sync_restore[1] if len(args.sync_restore) == 2 else None
        try:
            root_id, home_id = find_coordinated_pair(target_date, target_desc)
            print(f"[*] Found coordinated snapshot pair: Root={root_id} Home={home_id}", file=sys.stderr)
            handle_restore_pair("root", root_id, "home", home_id)
        except RuntimeError as e: fail(str(e))
        
    elif args.sync_delete is not None:
        if len(args.sync_delete) < 1 or len(args.sync_delete) > 2:
            parser.error("--sync-delete requires 1 or 2 arguments: TARGET_DATE [TARGET_DESC]")
        target_date = args.sync_delete[0]
        target_desc = args.sync_delete[1] if len(args.sync_delete) == 2 else None
        try:
            root_id, home_id = find_coordinated_pair(target_date, target_desc)
            print(f"[*] Found coordinated snapshot pair: Root={root_id} Home={home_id}", file=sys.stderr)
            handle_delete_pair("root", root_id, "home", home_id)
        except RuntimeError as e: fail(str(e))

if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--tui-preview":
        _view = sys.argv[2]
        _show_diff = "--show-diff" in sys.argv
        _remaining = [a for a in sys.argv[3:] if a != "--show-diff"]
        handle_tui_preview(_view, " ".join(_remaining), show_diff=_show_diff)
        sys.exit(0)
    try:
        main()
    except KeyboardInterrupt:
        print("\n\033[1;38;5;196m[!] Terminated by user.\033[0m", file=sys.stderr)
        sys.exit(130)
