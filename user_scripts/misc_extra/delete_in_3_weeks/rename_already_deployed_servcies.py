#!/usr/bin/env python3
"""
rename_already_deployed_servcies.py — Live-swap and migrate legacy Dusky systemd units

Idempotently inspects, live-swaps, and migrates deployed services, timers, and sockets
from legacy naming conventions (e.g., hyphens or legacy names) to canonical underscore names.

Features:
  - Detects active and enabled states of legacy units across system and user scopes.
  - Safely stops and disables legacy units with strict execution timeouts (no hung updates).
  - Atomically renames or supersedes unit files and drop-in configuration directories.
  - Cleans up obsolete wants/requires symlinks and broken legacy aliases.
  - Handles global user services (systemctl --global) and per-user session daemons.
  - Validates active D-Bus and runtime socket availability before dispatching user calls.
  - Reloads systemd daemons and restores enabled/active states on the new units.
  - Fully idempotent, crash-resilient (never fails), and safe for unattended updater runs.
"""

from __future__ import annotations

import argparse
import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

# --- ANSI Formatting ---
class C:
    RED = "\033[1;31m"
    GRN = "\033[1;32m"
    YLW = "\033[1;33m"
    BLU = "\033[1;34m"
    CYN = "\033[1;36m"
    DIM = "\033[2m"
    RST = "\033[0m"

if not sys.stdout.isatty() or "NO_COLOR" in os.environ:
    C.RED = C.GRN = C.YLW = C.BLU = C.CYN = C.DIM = C.RST = ""

def info(msg: str) -> None:
    print(f"{C.BLU}[INFO]{C.RST} {msg}")

def ok(msg: str) -> None:
    print(f"{C.GRN}[ OK ]{C.RST} {msg}")

def warn(msg: str) -> None:
    print(f"{C.YLW}[WARN]{C.RST} {msg}")

def swap(old: str, new: str, details: str = "") -> None:
    det = f" ({details})" if details else ""
    print(f"{C.CYN}[SWAP]{C.RST} {old} -> {new}{det}")

def purge(target: str) -> None:
    print(f"{C.YLW}[PURGE]{C.RST} Stale artifact: {target}")


class MigrationRule(NamedTuple):
    old_unit: str
    new_unit: str  # Empty string means purge legacy unit without replacement


# System-level units (/etc/systemd/system)
SYSTEM_MIGRATIONS: list[MigrationRule] = [
    # Hyphen -> Underscore transitions
    MigrationRule("dusky-kbd-backlight.service", "dusky_kbd_backlight.service"),
    MigrationRule("dusky-zram-recompress.service", "dusky_zram_recompress.service"),
    MigrationRule("dusky-zram-recompress.timer", "dusky_zram_recompress.timer"),
    # Legacy typos and obsolete units
    MigrationRule("dusky_snaapshot.service", "dusky_snapshot.service"),
    MigrationRule("dusky_snaapshot.timer", "dusky_snapshot.timer"),
    MigrationRule("dusky-tune.service", ""),
]

# User-level units (~/.config/systemd/user and /etc/systemd/user)
USER_MIGRATIONS: list[MigrationRule] = [
    # Renamed VNC & display services
    MigrationRule("dusky_vnc.service", "dusky_vnc_desktop.service"),
    MigrationRule("dusky_phone_display.service", "dusky_vnc_display.service"),
    MigrationRule("dusky-vnc.service", "dusky_vnc_desktop.service"),
    MigrationRule("dusky-vnc-desktop.service", "dusky_vnc_desktop.service"),
    MigrationRule("dusky-phone-display.service", "dusky_vnc_display.service"),
    MigrationRule("dusky-vnc-display.service", "dusky_vnc_display.service"),
    # Kokoro TTS services & socket
    MigrationRule("dusky-kokoro.service", "dusky_kokoro.service"),
    MigrationRule("dusky-kokoro.socket", "dusky_kokoro.socket"),
    MigrationRule("dusky-kokoro-adhoc.service", "dusky_kokoro_adhoc.service"),
    # OOM Shield daemon
    MigrationRule("dusky-oom-shield.service", "dusky_oom_shield.service"),
    # Hyphenated variants of other standard user units
    MigrationRule("dusky-visualizer.service", "dusky_visualizer.service"),
    MigrationRule("dusky-clipboard.service", "dusky_clipboard.service"),
    MigrationRule("dusky-battery.service", "dusky_battery.service"),
    MigrationRule("dusky-polkit.service", "dusky_polkit.service"),
    MigrationRule("dusky-notif-time.service", "dusky_notif_time.service"),
    MigrationRule("dusky-ram-monitor.service", "dusky_ram_monitor.service"),
    MigrationRule("dusky-screentime.service", "dusky_screentime.service"),
    MigrationRule("dusky-stt.service", "dusky_stt.service"),
    MigrationRule("dusky-moonlight-display.service", "dusky_moonlight_display.service"),
]

# Default per-command timeout in seconds to prevent stalling the updater
DEFAULT_TIMEOUT_SEC = 15.0


def run_cmd(
    cmd: list[str],
    env: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> subprocess.CompletedProcess:
    """Run command safely with timeout and structured fallback."""
    try:
        return subprocess.run(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, returncode=124, stdout="", stderr="Command timed out")
    except Exception as e:
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr=str(e))


def is_user_bus_available(uid: int) -> bool:
    """Check if the user's systemd user bus or private socket is accessible."""
    runtime = Path(f"/run/user/{uid}")
    if not runtime.is_dir():
        return False
    return (runtime / "systemd" / "private").exists() or (runtime / "bus").exists()


def get_user_sessions() -> list[tuple[int, str]]:
    """Discover user accounts that have active sessions or configuration trees."""
    users: dict[int, str] = {}

    # 1. SUDO_USER if running elevated
    sudo_user = os.environ.get("SUDO_USER")
    sudo_uid = os.environ.get("SUDO_UID")
    if sudo_user and sudo_uid:
        try:
            users[int(sudo_uid)] = sudo_user
        except ValueError:
            pass

    # 2. Active login sessions from loginctl
    res = run_cmd(["loginctl", "list-sessions", "--no-legend"], timeout=5.0)
    if res.returncode == 0:
        for line in res.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 3:
                try:
                    uid = int(parts[1])
                    user = parts[2]
                    if uid >= 1000:
                        users[uid] = user
                except (ValueError, IndexError):
                    continue

    # 3. Active runtime directories in /run/user/
    run_user = Path("/run/user")
    if run_user.is_dir():
        try:
            for entry in run_user.iterdir():
                if entry.is_dir() and entry.name.isdigit():
                    uid = int(entry.name)
                    if uid >= 1000 and uid not in users:
                        try:
                            pw = pwd.getpwuid(uid)
                            users[uid] = pw.pw_name
                        except (KeyError, OSError):
                            pass
        except OSError:
            pass

    # 4. If current unprivileged user, include self
    euid = os.geteuid()
    if euid != 0 and euid not in users:
        try:
            users[euid] = pwd.getpwuid(euid).pw_name
        except (KeyError, OSError):
            pass

    return list(users.items())


def run_systemctl(
    args: list[str],
    user_ctx: tuple[int, str] | None = None,
    global_user: bool = False,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> subprocess.CompletedProcess:
    """Execute systemctl either system-wide, globally, or for a specific user session."""
    if user_ctx:
        uid, username = user_ctx
        if not is_user_bus_available(uid):
            return subprocess.CompletedProcess(["systemctl", "--user", *args], returncode=3, stdout="", stderr="User bus unavailable")

        if os.geteuid() == 0:
            runtime = f"/run/user/{uid}"
            env = os.environ.copy()
            env["XDG_RUNTIME_DIR"] = runtime
            bus_path = f"{runtime}/bus"
            if os.path.exists(bus_path):
                env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus_path}"

            if shutil.which("runuser"):
                cmd = ["runuser", "-u", username, "-w", "XDG_RUNTIME_DIR,DBUS_SESSION_BUS_ADDRESS", "--", "systemctl", "--user", *args]
                return run_cmd(cmd, env=env, timeout=timeout)
            else:
                cmd = ["su", "-", username, "-c", f"systemctl --user {' '.join(args)}"]
                return run_cmd(cmd, env=env, timeout=timeout)
        else:
            return run_cmd(["systemctl", "--user", *args], timeout=timeout)

    cmd = ["systemctl"]
    if global_user:
        cmd.append("--global")
    cmd.extend(args)
    return run_cmd(cmd, timeout=timeout)


def migrate_unit_files(
    unit_dir: Path,
    old_unit: str,
    new_unit: str,
    dry_run: bool = False,
    owner_uid: int = -1,
    owner_gid: int = -1,
) -> bool:
    """Safely migrate unit file, drop-in override folder, and remove stale symlinks on disk."""
    if not unit_dir.exists():
        return False

    changed = False
    old_file = unit_dir / old_unit
    new_file = unit_dir / new_unit if new_unit else None

    # 1. Manage unit file
    if old_file.exists() or old_file.is_symlink():
        if dry_run:
            info(f"[dry-run] Would migrate file: {old_file} -> {new_file}")
            changed = True
        else:
            try:
                if new_file and new_file.exists():
                    old_file.unlink(missing_ok=True)
                    changed = True
                elif new_file:
                    old_file.rename(new_file)
                    if owner_uid >= 0 and owner_gid >= 0:
                        try:
                            os.chown(new_file, owner_uid, owner_gid)
                        except OSError:
                            pass
                    changed = True
                else:
                    # Purge only
                    old_file.unlink(missing_ok=True)
                    changed = True
            except OSError as e:
                warn(f"Failed to migrate unit file {old_file}: {e}")

    # 2. Manage drop-in directories (e.g. old_unit.d/)
    old_dropin = unit_dir / f"{old_unit}.d"
    new_dropin = unit_dir / f"{new_unit}.d" if new_unit else None

    if old_dropin.is_dir():
        if dry_run:
            info(f"[dry-run] Would migrate drop-in: {old_dropin} -> {new_dropin}")
            changed = True
        else:
            try:
                if new_dropin and not new_dropin.exists():
                    old_dropin.rename(new_dropin)
                    if owner_uid >= 0 and owner_gid >= 0:
                        try:
                            os.chown(new_dropin, owner_uid, owner_gid)
                        except OSError:
                            pass
                    changed = True
                elif new_dropin and new_dropin.exists():
                    for item in old_dropin.iterdir():
                        dest = new_dropin / item.name
                        if not dest.exists():
                            shutil.move(str(item), str(dest))
                    shutil.rmtree(str(old_dropin), ignore_errors=True)
                    changed = True
                else:
                    shutil.rmtree(str(old_dropin), ignore_errors=True)
                    changed = True
            except OSError as e:
                warn(f"Failed to migrate drop-in directory {old_dropin}: {e}")

    # 3. Clean stale symlinks in .wants/.requires folders under unit_dir
    try:
        patterns = (
            f"*/*.wants/{old_unit}",
            f"*.wants/{old_unit}",
            f"*/*.requires/{old_unit}",
            f"*.requires/{old_unit}",
        )
        for pat in patterns:
            for symlink in unit_dir.glob(pat):
                if symlink.is_symlink() or symlink.exists():
                    if dry_run:
                        info(f"[dry-run] Would remove stale symlink: {symlink}")
                    else:
                        symlink.unlink(missing_ok=True)
                    changed = True
    except OSError:
        pass

    return changed


def live_swap_service(
    rule: MigrationRule,
    user_ctx: tuple[int, str] | None = None,
    dry_run: bool = False,
) -> bool:
    """Inspect and live-swap a service in systemd runtime."""
    old_unit = rule.old_unit
    new_unit = rule.new_unit
    scope_str = f"user({user_ctx[1]})" if user_ctx else "system"

    # Step 1: Detect active & enabled states
    res_active = run_systemctl(["is-active", "--quiet", old_unit], user_ctx=user_ctx)
    was_active = (res_active.returncode == 0)

    res_enabled = run_systemctl(["is-enabled", "--quiet", old_unit], user_ctx=user_ctx)
    was_enabled = (res_enabled.returncode == 0)

    # Check if failed
    res_failed = run_systemctl(["is-failed", "--quiet", old_unit], user_ctx=user_ctx)
    was_failed = (res_failed.returncode == 0)

    # If neither active nor enabled, clean any lingering failed state and exit
    if not (was_active or was_enabled):
        if was_failed and not dry_run:
            run_systemctl(["reset-failed", old_unit], user_ctx=user_ctx)
        return False

    if dry_run:
        swap(old_unit, new_unit or "[PURGE]", f"{scope_str}, active={was_active}, enabled={was_enabled} [dry-run]")
        return True

    # Step 2: Stop and disable old unit
    if was_active:
        run_systemctl(["stop", old_unit], user_ctx=user_ctx)
    if was_enabled:
        run_systemctl(["disable", old_unit], user_ctx=user_ctx)

    # Step 3: Daemon reload to flush old unit definition
    run_systemctl(["daemon-reload"], user_ctx=user_ctx)

    # Step 4: Enable and start new unit if replacement exists and old unit was enabled/active
    details: list[str] = []
    if new_unit:
        if was_enabled:
            r = run_systemctl(["enable", new_unit], user_ctx=user_ctx)
            if r.returncode == 0:
                details.append("enabled")
        if was_active:
            r = run_systemctl(["start", new_unit], user_ctx=user_ctx)
            if r.returncode == 0:
                details.append("started")
            else:
                warn(f"{scope_str}: Notice: {new_unit} start deferred ({r.stderr.strip() or 'inactive'})")

        run_systemctl(["reset-failed", old_unit], user_ctx=user_ctx)
        run_systemctl(["reset-failed", new_unit], user_ctx=user_ctx)

    swap(old_unit, new_unit or "[PURGED]", f"{scope_str}: {', '.join(details) or 'migrated'}")
    return True


def clean_dangling_dusky_symlinks(search_dirs: list[Path], dry_run: bool = False) -> int:
    """Purge broken symlinks pointing to non-existent dusky units."""
    removed = 0
    for root_dir in search_dirs:
        if not root_dir.exists():
            continue
        try:
            for item in root_dir.rglob("*dusky*"):
                if item.is_symlink() and not item.exists():
                    if dry_run:
                        info(f"[dry-run] Would purge broken symlink: {item}")
                    else:
                        purge(str(item))
                        item.unlink(missing_ok=True)
                    removed += 1
        except OSError:
            pass
    return removed


def migrate_system_scope(dry_run: bool = False) -> bool:
    """Migrate all system-level services and unit files."""
    if os.geteuid() != 0:
        return False

    system_dir = Path("/etc/systemd/system")
    systemd_reloaded = False
    activity = False

    # 1. Disk migrations
    for rule in SYSTEM_MIGRATIONS:
        if migrate_unit_files(system_dir, rule.old_unit, rule.new_unit, dry_run=dry_run):
            activity = True
            systemd_reloaded = True

    # 2. Clean dangling symlinks
    if clean_dangling_dusky_symlinks([system_dir], dry_run=dry_run) > 0:
        activity = True
        systemd_reloaded = True

    # 3. Reload daemon if disk files changed
    if systemd_reloaded and not dry_run:
        run_systemctl(["daemon-reload"])

    # 4. Live swap runtime services
    for rule in SYSTEM_MIGRATIONS:
        if live_swap_service(rule, user_ctx=None, dry_run=dry_run):
            activity = True

    return activity


def migrate_user_global_scope(dry_run: bool = False) -> bool:
    """Migrate global user services in /etc/systemd/user."""
    if os.geteuid() != 0:
        return False

    user_global_dir = Path("/etc/systemd/user")
    activity = False

    # 1. Disk migrations
    for rule in USER_MIGRATIONS:
        if migrate_unit_files(user_global_dir, rule.old_unit, rule.new_unit, dry_run=dry_run):
            activity = True

    if clean_dangling_dusky_symlinks([user_global_dir], dry_run=dry_run) > 0:
        activity = True

    # 2. Check global enablement (systemctl --global)
    for rule in USER_MIGRATIONS:
        res = run_systemctl(["is-enabled", "--quiet", rule.old_unit], global_user=True)
        if res.returncode == 0:
            if dry_run:
                swap(rule.old_unit, rule.new_unit or "[PURGE]", "global user: enabled [dry-run]")
                activity = True
            else:
                run_systemctl(["disable", "--quiet", rule.old_unit], global_user=True)
                if rule.new_unit:
                    run_systemctl(["enable", "--quiet", rule.new_unit], global_user=True)
                    swap(rule.old_unit, rule.new_unit, "global user: enabled")
                else:
                    purge(f"global user: {rule.old_unit}")
                activity = True

    return activity


def migrate_user_scope(dry_run: bool = False) -> bool:
    """Migrate user-level services across active sessions and home directories."""
    activity = False
    users = get_user_sessions()

    # If running as non-root, ensure current user is processed
    if not users and os.geteuid() != 0:
        try:
            pw = pwd.getpwuid(os.geteuid())
            users = [(pw.pw_uid, pw.pw_name)]
        except Exception:
            pass

    # A. Process active runtime user systemd managers
    for user_ctx in users:
        uid, username = user_ctx
        reload_needed = False

        # 1. User config directory on disk
        try:
            pw = pwd.getpwnam(username)
            user_config_dir = Path(pw.pw_dir) / ".config" / "systemd" / "user"
            owner_uid, owner_gid = pw.pw_uid, pw.pw_gid
        except (KeyError, OSError):
            user_config_dir = Path(f"/home/{username}/.config/systemd/user")
            owner_uid, owner_gid = -1, -1

        for rule in USER_MIGRATIONS:
            if migrate_unit_files(
                user_config_dir,
                rule.old_unit,
                rule.new_unit,
                dry_run=dry_run,
                owner_uid=owner_uid,
                owner_gid=owner_gid,
            ):
                activity = True
                reload_needed = True

        if clean_dangling_dusky_symlinks([user_config_dir], dry_run=dry_run) > 0:
            activity = True
            reload_needed = True

        if reload_needed and not dry_run:
            run_systemctl(["daemon-reload"], user_ctx=user_ctx)

        # 2. Live-swap runtime user services if user bus is active
        if is_user_bus_available(uid):
            for rule in USER_MIGRATIONS:
                if live_swap_service(rule, user_ctx=user_ctx, dry_run=dry_run):
                    activity = True

    # B. Also inspect non-logged-in home directories if root
    if os.geteuid() == 0:
        home_root = Path("/home")
        if home_root.is_dir():
            try:
                for user_dir in home_root.iterdir():
                    if user_dir.is_dir():
                        cfg_dir = user_dir / ".config" / "systemd" / "user"
                        if cfg_dir.is_dir():
                            try:
                                pw = pwd.getpwnam(user_dir.name)
                                uid, gid = pw.pw_uid, pw.pw_gid
                            except KeyError:
                                uid, gid = -1, -1
                            for rule in USER_MIGRATIONS:
                                if migrate_unit_files(cfg_dir, rule.old_unit, rule.new_unit, dry_run=dry_run, owner_uid=uid, owner_gid=gid):
                                    activity = True
                            if clean_dangling_dusky_symlinks([cfg_dir], dry_run=dry_run) > 0:
                                activity = True
            except OSError:
                pass

    return activity


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Live-swap and migrate legacy Dusky systemd units safely and idempotently."
    )
    parser.add_argument("--dry-run", action="store_true", help="Inspect and report without modifying system")
    args = parser.parse_args()

    info("Inspecting system and user services for legacy unit migrations...")

    try:
        sys_changed = migrate_system_scope(dry_run=args.dry_run)
        global_changed = migrate_user_global_scope(dry_run=args.dry_run)
        user_changed = migrate_user_scope(dry_run=args.dry_run)

        if sys_changed or global_changed or user_changed:
            ok("Migration complete: Legacy Dusky services successfully live-swapped.")
        else:
            ok("All Dusky services verified up to date (no migration required).")
    except KeyboardInterrupt:
        warn("Migration interrupted by user signal.")
    except BaseException as e:
        # Guarantee zero crash exit under any circumstance to prevent tripping up the updater
        warn(f"Encountered non-fatal exception during migration: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
