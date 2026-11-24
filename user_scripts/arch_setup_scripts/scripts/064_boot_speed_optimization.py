#!/usr/bin/env python3
#d: Deploy Dusky boot and shutdown speed optimizations
"""Reproducible deployment and verification of Arch Linux boot/shutdown speed optimizations.

Targets rolling-release Arch Linux (Linux 7.3+, Python 3.15+, systemd 262+, Wayland/Hyprland)
across both booted live hosts and offline ISO chroot environments.
"""

from __future__ import annotations

import argparse
import errno
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

# ==============================================================================
# ANSI Presentation & Structured Logging
# ==============================================================================

_USE_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ


class C:
    RED = "\033[1;31m" if _USE_COLOR else ""
    GRN = "\033[1;32m" if _USE_COLOR else ""
    YLW = "\033[1;33m" if _USE_COLOR else ""
    BLU = "\033[1;34m" if _USE_COLOR else ""
    CYN = "\033[1;36m" if _USE_COLOR else ""
    DIM = "\033[2m" if _USE_COLOR else ""
    BOLD = "\033[1m" if _USE_COLOR else ""
    RST = "\033[0m" if _USE_COLOR else ""


def info(msg: str) -> None:
    print(f"{C.BLU}[INFO]{C.RST} {msg}")


def ok(msg: str) -> None:
    print(f"{C.GRN}[PASS]{C.RST} {msg}")


def action(msg: str) -> None:
    print(f"{C.CYN}[ACTION]{C.RST} {msg}")


def skip(msg: str) -> None:
    print(f"{C.DIM}[SKIP]{C.RST} {msg}")


def warn(msg: str) -> None:
    print(f"{C.YLW}[WARN]{C.RST} {msg}")


def err(msg: str) -> None:
    print(f"{C.RED}[FAIL]{C.RST} {msg}", file=sys.stderr)


def die(msg: str, code: int = 1) -> NoReturn:
    err(msg)
    sys.exit(code)


# ==============================================================================
# Managed Artifact Paths & Canonical Payloads
# ==============================================================================

# 1. systemd-user-sessions.service full override (omits network.target from After=)
USER_SESSIONS_SERVICE = Path("/etc/systemd/system/systemd-user-sessions.service")
USER_SESSIONS_BIN = Path("/usr/lib/systemd/systemd-user-sessions")
USER_SESSIONS_CONTENT = """#  SPDX-License-Identifier: LGPL-2.1-or-later
#
#  This file is part of systemd.
#
#  systemd is free software; you can redistribute it and/or modify it
#  under the terms of the GNU Lesser General Public License as published by
#  the Free Software Foundation; either version 2.1 of the License, or
#  (at your option) any later version.

[Unit]
Description=Permit User Sessions
Documentation=man:systemd-user-sessions.service(8)
After=remote-fs.target nss-user-lookup.target home.mount

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/lib/systemd/systemd-user-sessions start
ExecStop=/usr/lib/systemd/systemd-user-sessions stop
"""

# 2. linux-modules-cleanup.conf tmpfiles shadow rule
TMPFILES_MODULES_CLEANUP = Path("/etc/tmpfiles.d/linux-modules-cleanup.conf")
TMPFILES_MODULES_RULE = "e /usr/lib/modules/.old - - - 0"
TMPFILES_MODULES_CONTENT = f"{TMPFILES_MODULES_RULE}\n"

# 3. ldconfig.service boot drop-in
LDCONFIG_DROPIN_DIR = Path("/etc/systemd/system/ldconfig.service.d")
LDCONFIG_DROPIN_FILE = LDCONFIG_DROPIN_DIR / "10-dusky-boot.conf"
LDCONFIG_DROPIN_CONTENT = """[Unit]
ConditionNeedsUpdate=
ConditionFileNotEmpty=!/etc/ld.so.cache
"""

# 4. dusky-shutdown-ramfs.timer background pre-generator
SHUTDOWN_TIMER_NAME = "dusky-shutdown-ramfs.timer"
SHUTDOWN_TIMER_TARGET = "mkinitcpio-generate-shutdown-ramfs.service"
SHUTDOWN_TIMER_FILE = Path("/etc/systemd/system") / SHUTDOWN_TIMER_NAME
SHUTDOWN_TIMER_CONTENT = f"""[Unit]
Description=Generate shutdown-ramfs in background after boot
ConditionPathExists=/run/initramfs/mkinitcpio-shutdown.conf
ConditionFileIsExecutable=!/run/initramfs/shutdown

[Timer]
OnStartupSec=30s
AccuracySec=5s
Unit={SHUTDOWN_TIMER_TARGET}

[Install]
WantedBy=timers.target
"""

# 5. systemd user manager fast shutdown drop-in
USER_TIMEOUT_DIR = Path("/etc/systemd/user.conf.d")
USER_TIMEOUT_FILE = USER_TIMEOUT_DIR / "10-faster-shutdown.conf"
USER_TIMEOUT_VALUE = "2s"
USER_TIMEOUT_CONTENT = f"""[Manager]
DefaultTimeoutStopSec={USER_TIMEOUT_VALUE}
"""

SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")


# ==============================================================================
# Runtime Context & Subprocess Helpers
# ==============================================================================


@dataclass(frozen=True, slots=True)
class CmdResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class RuntimeEnv:
    live_systemd: bool


def run_cmd(cmd: list[str], *, timeout: int = 15, check: bool = False) -> CmdResult:
    """Execute an external command with bounded timeout and structured error reporting."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check,
        )
        return CmdResult(proc.returncode, proc.stdout, proc.stderr)
    except FileNotFoundError:
        if check:
            die(f"Required command not found: {cmd[0]}")
        return CmdResult(127, "", f"Command not found: {cmd[0]}")
    except subprocess.TimeoutExpired:
        if check:
            die(f"Command timed out after {timeout}s: {' '.join(cmd)}")
        return CmdResult(124, "", f"Timed out after {timeout}s")
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        die(f"Command failed ({exc.returncode}): {' '.join(cmd)}" + (f"\n{detail}" if detail else ""))


def _is_bind_mounted_chroot() -> bool:
    """Detect chroots with bind-mounted /run when root privileges allow /proc/1/root stat."""
    if os.geteuid() != 0:
        return False
    try:
        root_st = os.stat("/")
        init_st = os.stat("/proc/1/root")
        return (root_st.st_dev, root_st.st_ino) != (init_st.st_dev, init_st.st_ino)
    except OSError:
        return False


def detect_runtime_env() -> RuntimeEnv:
    live_systemd = Path("/run/systemd/system").is_dir() and not _is_bind_mounted_chroot()
    return RuntimeEnv(live_systemd=live_systemd)


def escalate_root_if_needed() -> None:
    """Re-exec with sudo (or pkexec fallback) when mutation requires root privileges."""
    if os.geteuid() == 0:
        return
    script_path = str(Path(__file__).resolve())
    argv = [sys.executable, script_path, *sys.argv[1:]]
    if shutil.which("sudo"):
        os.execvp("sudo", ["sudo", "--", *argv])
    if shutil.which("pkexec"):
        os.execvp("pkexec", ["pkexec", *argv])
    die("Root privileges required (neither sudo nor pkexec is available).")


# ==============================================================================
# Durable Atomic File Writer & Deployment Helper
# ==============================================================================


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    dir_fd = os.open(directory, flags)
    try:
        os.fsync(dir_fd)
    except OSError as exc:
        if exc.errno not in (errno.EINVAL, errno.EROFS):
            raise
    finally:
        os.close(dir_fd)


def atomic_write(target: Path, content: str, mode: int = 0o644) -> bool:
    """Atomically write UTF-8 content with fchmod, file fsync, replace, and parent dir fsync.

    Returns True if content, symlink state, or file mode changed; False if already exact.
    """
    is_symlink = target.is_symlink()
    if not is_symlink and target.exists():
        if target.is_dir():
            raise IsADirectoryError(f"Refusing to overwrite directory: {target}")
        current_text = target.read_text(encoding="utf-8")
        current_mode = stat.S_IMODE(target.lstat().st_mode)
        if current_text == content:
            if current_mode == mode:
                return False
            os.chmod(target, mode)
            _fsync_directory(target.parent)
            return True

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.tmp.",
            delete=False,
        ) as fh:
            tmp_path = Path(fh.name)
            os.fchmod(fh.fileno(), mode)
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target)
        _fsync_directory(target.parent)
        return True
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def deploy_file(target: Path, content: str, description: str, *, dry_run: bool, mode: int = 0o644) -> bool:
    """Idempotently deploy a managed file, supporting --dry-run and masked symlink replacement."""
    is_symlink = target.is_symlink()
    if is_symlink:
        warn(f"{target} is a symlink (-> {os.readlink(target)}); replacing with regular managed file.")
        if dry_run:
            action(f"[DRY-RUN] Would replace symlink {target} ({description})")
            return True
        atomic_write(target, content, mode=mode)
        action(f"Replaced symlink {target} ({description})")
        return True

    if target.exists():
        try:
            current_text = target.read_text(encoding="utf-8")
            current_mode = stat.S_IMODE(target.lstat().st_mode)
        except OSError:
            current_text = None
            current_mode = None

        if current_text == content and current_mode == mode:
            skip(f"{target} is up to date.")
            return False

        if current_text == content and current_mode != mode:
            if dry_run:
                action(f"[DRY-RUN] Would normalize permissions on {target} to {oct(mode)}")
                return True
            atomic_write(target, content, mode=mode)
            action(f"Normalized permissions on {target} to {oct(mode)}")
            return True

        verb = "update"
    else:
        verb = "create"

    if dry_run:
        action(f"[DRY-RUN] Would {verb} {target} ({description})")
        return True

    atomic_write(target, content, mode=mode)
    action(f"{'Updated' if verb == 'update' else 'Created'} {target} ({description})")
    return True


# ==============================================================================
# Configuration Parsing Helpers (Verification & Status)
# ==============================================================================


def parse_effective_user_timeout(cat_config_output: str) -> str | None:
    """Return the final effective DefaultTimeoutStopSec value in [Manager] from cat-config output."""
    current_section: str | None = None
    effective: str | None = None
    for line in cat_config_output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        sec_match = SECTION_RE.match(stripped)
        if sec_match:
            current_section = sec_match.group(1).strip()
            continue
        if current_section == "Manager":
            m = re.match(r"^DefaultTimeoutStopSec\s*=\s*(.*?)\s*$", stripped)
            if m:
                effective = m.group(1)
    return effective


def parse_tmpfiles_effective_lines(cat_config_output: str) -> list[str]:
    """Return active non-comment tmpfiles rules from `systemd-analyze cat-config` output."""
    rules: list[str] = []
    for line in cat_config_output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        rules.append(stripped)
    return rules


def unit_after_tokens(unit_text: str) -> list[str]:
    """Extract all unit names declared across `After=` lines in `[Unit]`."""
    tokens: list[str] = []
    current_section: str | None = None
    for line in unit_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        sec_match = SECTION_RE.match(stripped)
        if sec_match:
            current_section = sec_match.group(1).strip()
            continue
        if current_section == "Unit":
            m = re.match(r"^After\s*=\s*(.*?)\s*$", stripped)
            if m:
                if not m.group(1):
                    tokens.clear()
                else:
                    tokens.extend(m.group(1).split())
    return tokens


# ==============================================================================
# Apply Mode
# ==============================================================================


def apply_optimizations(env: RuntimeEnv, *, dry_run: bool = False) -> int:
    if not dry_run:
        escalate_root_if_needed()

    mode_label = "DRY-RUN PREVIEW" if dry_run else "APPLY"
    env_label = "Live Systemd" if env.live_systemd else "Offline / Chroot"
    print(f"\n{C.BOLD}=== Dusky Fast Boot & Shutdown Optimizations ({mode_label} | {env_label}) ==={C.RST}\n")

    changes = 0

    # 1. systemd-user-sessions.service full override
    if deploy_file(
        USER_SESSIONS_SERVICE,
        USER_SESSIONS_CONTENT,
        "omitting network.target from After=",
        dry_run=dry_run,
    ):
        changes += 1

    # 2. linux-modules-cleanup.conf tmpfiles override
    if deploy_file(
        TMPFILES_MODULES_CLEANUP,
        TMPFILES_MODULES_CONTENT,
        "deferring /usr/lib/modules/.old cleanup to idle clean",
        dry_run=dry_run,
    ):
        changes += 1

    # 3. ldconfig.service boot drop-in
    if deploy_file(
        LDCONFIG_DROPIN_FILE,
        LDCONFIG_DROPIN_CONTENT,
        "clearing ConditionNeedsUpdate= and guarding on /etc/ld.so.cache",
        dry_run=dry_run,
    ):
        changes += 1

    # 4. dusky-shutdown-ramfs.timer
    if deploy_file(
        SHUTDOWN_TIMER_FILE,
        SHUTDOWN_TIMER_CONTENT,
        "background shutdown-ramfs generator timer",
        dry_run=dry_run,
    ):
        changes += 1

    # 5. systemd user manager stop timeout
    if deploy_file(
        USER_TIMEOUT_FILE,
        USER_TIMEOUT_CONTENT,
        f"DefaultTimeoutStopSec={USER_TIMEOUT_VALUE}",
        dry_run=dry_run,
    ):
        changes += 1

    # Systemd reload & timer enablement
    timer_enabled = run_cmd(["systemctl", "is-enabled", SHUTDOWN_TIMER_NAME], timeout=10).stdout.strip() == "enabled"

    if dry_run:
        if env.live_systemd:
            action("[DRY-RUN] Would run: systemctl daemon-reload")
        else:
            info("Offline/chroot environment detected; daemon-reload would be skipped.")
        if timer_enabled:
            skip(f"{SHUTDOWN_TIMER_NAME} is already enabled.")
        else:
            action(f"[DRY-RUN] Would run: systemctl enable {SHUTDOWN_TIMER_NAME}")
            changes += 1
        print(f"\n{C.BOLD}Dry-run complete:{C.RST} {changes} planned change(s). No files were modified.")
        return 0

    if env.live_systemd:
        action("Reloading systemd manager configuration (systemctl daemon-reload)...")
        run_cmd(["systemctl", "daemon-reload"], timeout=60, check=True)
    else:
        info("Offline/chroot environment detected; skipping systemctl daemon-reload.")

    if timer_enabled:
        skip(f"{SHUTDOWN_TIMER_NAME} is already enabled.")
    else:
        action(f"Enabling {SHUTDOWN_TIMER_NAME} under timers.target...")
        run_cmd(["systemctl", "enable", SHUTDOWN_TIMER_NAME], timeout=60, check=True)

    print()
    return run_verification(env)


# ==============================================================================
# Built-In Verification Suite (--check & post-apply)
# ==============================================================================


def run_verification(env: RuntimeEnv) -> int:
    """Verify all 5 boot/shutdown optimization components across live and chroot hosts."""
    env_label = "Live Systemd" if env.live_systemd else "Offline / Chroot"
    print(f"{C.BOLD}=== Boot & Shutdown Optimization Verification ({env_label}) ==={C.RST}\n")

    passed = 0
    failed = 0
    skipped = 0

    def check_ok(msg: str) -> None:
        nonlocal passed
        ok(msg)
        passed += 1

    def check_fail(msg: str) -> None:
        nonlocal failed
        err(msg)
        failed += 1

    def check_skip(msg: str) -> None:
        nonlocal skipped
        skip(msg)
        skipped += 1

    def verify_regular_file(path: Path, expected_mode: int = 0o644) -> str | None:
        if path.is_symlink():
            check_fail(f"{path} is a symlink (-> {os.readlink(path)}), expected regular file.")
            return None
        if not path.is_file():
            check_fail(f"{path} does not exist.")
            return None
        actual_mode = stat.S_IMODE(path.lstat().st_mode)
        if actual_mode != expected_mode:
            check_fail(f"{path} has permissions {oct(actual_mode)}, expected {oct(expected_mode)}.")
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError:
            check_fail(f"{path} is not readable (permission denied).")
            return None
        except OSError as exc:
            check_fail(f"Failed reading {path}: {exc}")
            return None

    # --- Component 1: systemd-user-sessions.service ---
    c1_text = verify_regular_file(USER_SESSIONS_SERVICE)
    if c1_text is not None:
        static_after = unit_after_tokens(c1_text)
        if "network.target" not in static_after:
            check_ok(f"{USER_SESSIONS_SERVICE} omits network.target from After=")
        else:
            check_fail(f"{USER_SESSIONS_SERVICE} still includes network.target in After=: {static_after}")

        if USER_SESSIONS_BIN.is_file() and os.access(USER_SESSIONS_BIN, os.X_OK):
            check_ok(f"Exec binary present and executable: {USER_SESSIONS_BIN}")
        else:
            check_fail(f"Exec binary missing or non-executable: {USER_SESSIONS_BIN}")

        if env.live_systemd:
            frag = run_cmd(
                ["systemctl", "show", "-p", "FragmentPath", "--value", "systemd-user-sessions.service"]
            ).stdout.strip()
            after = run_cmd(
                ["systemctl", "show", "-p", "After", "--value", "systemd-user-sessions.service"]
            ).stdout.strip()
            if frag == str(USER_SESSIONS_SERVICE):
                check_ok(f"Live unit FragmentPath resolved to {frag}")
            else:
                check_fail(f"Live unit FragmentPath mismatch: '{frag}'")
            if "network.target" not in after.split():
                check_ok("Live systemd-user-sessions.service After= excludes network.target")
            else:
                check_fail(f"Live systemd-user-sessions.service After= still includes network.target: {after}")
        else:
            check_skip("Live PID 1 check for systemd-user-sessions.service skipped (offline/chroot).")

    # --- Component 2: linux-modules-cleanup.conf tmpfiles override ---
    c2_text = verify_regular_file(TMPFILES_MODULES_CLEANUP)
    if c2_text is not None:
        if c2_text.strip() == TMPFILES_MODULES_RULE:
            check_ok(f"{TMPFILES_MODULES_CLEANUP} contains exact rule '{TMPFILES_MODULES_RULE}'")
        else:
            check_fail(f"{TMPFILES_MODULES_CLEANUP} content mismatch: {c2_text.strip()!r}")

        cat_tmp = run_cmd(["systemd-analyze", "cat-config", "tmpfiles.d/linux-modules-cleanup.conf"])
        effective_rules = parse_tmpfiles_effective_lines(cat_tmp.stdout)
        if (
            cat_tmp.returncode == 0
            and f"# {TMPFILES_MODULES_CLEANUP}" in cat_tmp.stdout
            and effective_rules == [TMPFILES_MODULES_RULE]
        ):
            check_ok(f"tmpfiles resolution shadowed cleanly by {TMPFILES_MODULES_CLEANUP}")
        else:
            check_fail(
                f"tmpfiles cat-config resolution mismatch (rc={cat_tmp.returncode}, rules={effective_rules})"
            )

    # --- Component 3: ldconfig.service drop-in ---
    c3_text = verify_regular_file(LDCONFIG_DROPIN_FILE)
    if c3_text is not None:
        c3_lines = [line.strip() for line in c3_text.splitlines() if line.strip()]
        if "ConditionNeedsUpdate=" in c3_lines and "ConditionFileNotEmpty=!/etc/ld.so.cache" in c3_lines:
            check_ok(f"{LDCONFIG_DROPIN_FILE} declares required Condition overrides")
        else:
            check_fail(f"{LDCONFIG_DROPIN_FILE} missing expected Condition directives: {c3_lines}")

        if env.live_systemd:
            dropins = run_cmd(
                ["systemctl", "show", "-p", "DropInPaths", "--value", "ldconfig.service"]
            ).stdout.strip()
            if str(LDCONFIG_DROPIN_FILE) in dropins.split():
                check_ok(f"Live ldconfig.service loaded drop-in {LDCONFIG_DROPIN_FILE}")
            else:
                check_fail(f"Drop-in missing from live ldconfig.service DropInPaths: '{dropins}'")

            # systemd-analyze condition exits non-zero when ConditionFileNotEmpty=!/etc/ld.so.cache
            # evaluates false (the desired fast-boot state), so inspect combined output regardless of rc.
            cond_res = run_cmd(["systemd-analyze", "condition", "--unit=ldconfig.service"])
            cond_output = f"{cond_res.stdout}\n{cond_res.stderr}"
            cond_lines = [line.strip() for line in cond_output.splitlines() if line.strip()]

            if not any("ConditionNeedsUpdate=" in line for line in cond_lines):
                check_ok("ConditionNeedsUpdate= cleared from effective ldconfig.service conditions")
            else:
                check_fail(f"ConditionNeedsUpdate= still active in ldconfig.service:\n{cond_output.strip()}")

            if any("ConditionFileNotEmpty=!/etc/ld.so.cache" in line for line in cond_lines):
                check_ok("ConditionFileNotEmpty=!/etc/ld.so.cache active on ldconfig.service")
            else:
                check_fail(f"ConditionFileNotEmpty=!/etc/ld.so.cache missing from ldconfig.service:\n{cond_output.strip()}")
        else:
            check_skip("Live PID 1 condition check for ldconfig.service skipped (offline/chroot).")

    # --- Component 4: dusky-shutdown-ramfs.timer ---
    c4_text = verify_regular_file(SHUTDOWN_TIMER_FILE)
    if c4_text is not None:
        required_directives = (
            "ConditionPathExists=/run/initramfs/mkinitcpio-shutdown.conf",
            "ConditionFileIsExecutable=!/run/initramfs/shutdown",
            "OnStartupSec=30s",
            "AccuracySec=5s",
            f"Unit={SHUTDOWN_TIMER_TARGET}",
            "WantedBy=timers.target",
        )
        c4_lines = {line.strip() for line in c4_text.splitlines()}
        missing_dirs = [d for d in required_directives if d not in c4_lines]
        if not missing_dirs:
            check_ok(f"{SHUTDOWN_TIMER_FILE} contains all required guard and timer directives")
        else:
            check_fail(f"{SHUTDOWN_TIMER_FILE} missing directives: {missing_dirs}")

        enabled = run_cmd(["systemctl", "is-enabled", SHUTDOWN_TIMER_NAME]).stdout.strip()
        if enabled == "enabled":
            check_ok(f"{SHUTDOWN_TIMER_NAME} is enabled")
        else:
            check_fail(f"{SHUTDOWN_TIMER_NAME} enablement status is '{enabled}' (expected 'enabled')")

        if env.live_systemd:
            load_state = run_cmd(
                ["systemctl", "show", "-p", "LoadState", "--value", SHUTDOWN_TIMER_NAME]
            ).stdout.strip()
            triggers = run_cmd(
                ["systemctl", "show", "-p", "Triggers", "--value", SHUTDOWN_TIMER_NAME]
            ).stdout.strip()
            if load_state == "loaded":
                check_ok(f"Live {SHUTDOWN_TIMER_NAME} LoadState is loaded")
            else:
                check_fail(f"Live {SHUTDOWN_TIMER_NAME} LoadState is '{load_state}'")
            if triggers == SHUTDOWN_TIMER_TARGET:
                check_ok(f"Live {SHUTDOWN_TIMER_NAME} triggers {triggers}")
            else:
                check_fail(f"Live {SHUTDOWN_TIMER_NAME} Triggers='{triggers}' (expected '{SHUTDOWN_TIMER_TARGET}')")
        else:
            check_skip(f"Live PID 1 property check for {SHUTDOWN_TIMER_NAME} skipped (offline/chroot).")

    # --- Component 5: systemd user manager stop timeout ---
    c5_text = verify_regular_file(USER_TIMEOUT_FILE)
    if c5_text is not None:
        cat_user = run_cmd(["systemd-analyze", "cat-config", "systemd/user.conf"])
        effective_timeout = parse_effective_user_timeout(cat_user.stdout)
        if cat_user.returncode == 0 and f"# {USER_TIMEOUT_FILE}" in cat_user.stdout:
            check_ok(f"User manager drop-in loaded: {USER_TIMEOUT_FILE}")
        else:
            check_fail(f"{USER_TIMEOUT_FILE} not reflected in `systemd-analyze cat-config systemd/user.conf`")

        if effective_timeout == USER_TIMEOUT_VALUE:
            check_ok(f"Effective user.conf DefaultTimeoutStopSec={effective_timeout} (not overridden)")
        else:
            check_fail(
                f"Effective user.conf DefaultTimeoutStopSec={effective_timeout!r} (expected {USER_TIMEOUT_VALUE!r})"
            )

    summary_color = C.GRN if failed == 0 else C.RED
    print(
        f"\n{C.BOLD}Verification Result:{C.RST} "
        f"{summary_color}{passed} passed, {failed} failed{C.RST}"
        + (f", {C.DIM}{skipped} skipped{C.RST}" if skipped else "")
    )
    return 0 if failed == 0 else 1


# ==============================================================================
# Non-Root Status Overview (--status)
# ==============================================================================


def _state_badge(state: str) -> str:
    if state in ("Active", "Enabled", "Shadowed"):
        return f"{C.GRN}{state}{C.RST}"
    if state in ("Staged", "Skipped"):
        return f"{C.YLW}{state}{C.RST}"
    return f"{C.RED}{state}{C.RST}"


def print_status(env: RuntimeEnv) -> int:
    """Print a non-root, read-only status overview across all 5 managed components."""
    env_label = "Live Systemd" if env.live_systemd else "Offline / Chroot"

    print(f"\n{C.BOLD}=== DUSKY BOOT & SHUTDOWN OPTIMIZATION STATUS ==={C.RST}")
    print(f"  {C.DIM}Environment:{C.RST} {env_label}\n")

    # 1. systemd-user-sessions.service
    if USER_SESSIONS_SERVICE.is_symlink():
        s1_state, s1_detail = "Drifted", f"symlink -> {os.readlink(USER_SESSIONS_SERVICE)}"
    elif not USER_SESSIONS_SERVICE.is_file():
        s1_state, s1_detail = "Missing", str(USER_SESSIONS_SERVICE)
    else:
        text = USER_SESSIONS_SERVICE.read_text(encoding="utf-8", errors="replace")
        has_net = "network.target" in unit_after_tokens(text)
        if has_net:
            s1_state, s1_detail = "Drifted", "network.target still present in After="
        elif env.live_systemd:
            live_after = run_cmd(
                ["systemctl", "show", "-p", "After", "--value", "systemd-user-sessions.service"]
            ).stdout.split()
            if "network.target" not in live_after:
                s1_state, s1_detail = "Active", "unblocked from network.target"
            else:
                s1_state, s1_detail = "Staged", "file updated; daemon-reload pending"
        else:
            s1_state, s1_detail = "Staged", "unblocked from network.target (offline)"
    print(f"  1. {C.CYN}systemd-user-sessions.service:{C.RST}  {_state_badge(s1_state)} ({s1_detail})")

    # 2. linux-modules-cleanup.conf
    if not TMPFILES_MODULES_CLEANUP.is_file() or TMPFILES_MODULES_CLEANUP.is_symlink():
        s2_state, s2_detail = "Missing", str(TMPFILES_MODULES_CLEANUP)
    else:
        cat_tmp = run_cmd(["systemd-analyze", "cat-config", "tmpfiles.d/linux-modules-cleanup.conf"])
        rules = parse_tmpfiles_effective_lines(cat_tmp.stdout)
        if rules == [TMPFILES_MODULES_RULE]:
            s2_state, s2_detail = "Shadowed", TMPFILES_MODULES_RULE
        else:
            s2_state, s2_detail = "Drifted", f"effective rules={rules}"
    print(f"  2. {C.CYN}linux-modules-cleanup.conf:{C.RST}     {_state_badge(s2_state)} ({s2_detail})")

    # 3. ldconfig.service drop-in
    if not LDCONFIG_DROPIN_FILE.is_file() or LDCONFIG_DROPIN_FILE.is_symlink():
        s3_state, s3_detail = "Missing", str(LDCONFIG_DROPIN_FILE)
    elif env.live_systemd:
        dropins = run_cmd(["systemctl", "show", "-p", "DropInPaths", "--value", "ldconfig.service"]).stdout.split()
        if str(LDCONFIG_DROPIN_FILE) in dropins:
            s3_state, s3_detail = "Active", "ConditionFileNotEmpty=!/etc/ld.so.cache"
        else:
            s3_state, s3_detail = "Staged", "drop-in present; daemon-reload pending"
    else:
        s3_state, s3_detail = "Staged", "drop-in present (offline)"
    print(f"  3. {C.CYN}ldconfig.service drop-in:{C.RST}       {_state_badge(s3_state)} ({s3_detail})")

    # 4. dusky-shutdown-ramfs.timer
    if not SHUTDOWN_TIMER_FILE.is_file() or SHUTDOWN_TIMER_FILE.is_symlink():
        s4_state, s4_detail = "Missing", str(SHUTDOWN_TIMER_FILE)
    else:
        enabled = run_cmd(["systemctl", "is-enabled", SHUTDOWN_TIMER_NAME]).stdout.strip()
        if enabled == "enabled":
            if env.live_systemd:
                active = run_cmd(["systemctl", "is-active", SHUTDOWN_TIMER_NAME]).stdout.strip()
                s4_state, s4_detail = "Enabled", f"OnStartupSec=30s; runtime={active}"
            else:
                s4_state, s4_detail = "Enabled", "OnStartupSec=30s (offline)"
        else:
            s4_state, s4_detail = "Drifted", f"is-enabled={enabled or 'unknown'}"
    print(f"  4. {C.CYN}dusky-shutdown-ramfs.timer:{C.RST}     {_state_badge(s4_state)} ({s4_detail})")

    # 5. user manager fast shutdown timeout
    if not USER_TIMEOUT_FILE.is_file() or USER_TIMEOUT_FILE.is_symlink():
        s5_state, s5_detail = "Missing", str(USER_TIMEOUT_FILE)
    else:
        cat_user = run_cmd(["systemd-analyze", "cat-config", "systemd/user.conf"])
        effective = parse_effective_user_timeout(cat_user.stdout)
        if effective == USER_TIMEOUT_VALUE:
            s5_state, s5_detail = "Active", f"DefaultTimeoutStopSec={effective}"
        else:
            s5_state, s5_detail = "Drifted", f"effective DefaultTimeoutStopSec={effective}"
    print(f"  5. {C.CYN}user.conf.d fast shutdown:{C.RST}      {_state_badge(s5_state)} ({s5_detail})\n")
    return 0


# ==============================================================================
# CLI Entry Point
# ==============================================================================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dusky Arch Linux Boot & Shutdown Speed Optimizer (Linux 7.3+, systemd 262+, Hyprland)"
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--apply",
        action="store_true",
        help="Deploy all 5 optimizations idempotently and run verification (default mode)",
    )
    mode_group.add_argument(
        "--check",
        "-c",
        action="store_true",
        help="Run read-only verification checks without mutating the system (exit 0 pass / 1 fail)",
    )
    mode_group.add_argument(
        "--status",
        "-s",
        action="store_true",
        help="Display non-root overview of active boot & shutdown optimization states",
    )
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Preview planned changes for --apply without writing files or escalating privileges",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.dry_run and (args.check or args.status):
        parser.error("--dry-run can only be used with --apply (the default mode).")

    env = detect_runtime_env()

    if args.status:
        return print_status(env)
    if args.check:
        return run_verification(env)
    return apply_optimizations(env, dry_run=args.dry_run)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(f"\n{C.YLW}Operation cancelled by user.{C.RST}", file=sys.stderr)
        sys.exit(130)
