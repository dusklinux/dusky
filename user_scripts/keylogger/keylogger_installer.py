#!/usr/bin/env python3
"""Dusky Keylogger installer.

Automates setup on Arch Linux (systemd):

  1. Auto-escalates to root via sudo when run without privileges.
  2. Adds the invoking user to the input group if they are missing
     (required to read /dev/input/event*).
  3. Creates an isolated venv at ~/contained_apps/uv/dusky_key_logger/
     (prefers uv with the installed system Python, else venv+pip on the same interpreter)
     and installs the package with its dependencies from pyproject.toml.
  4. Renders and installs dusky_keylogger.service so the daemon can run
     in the background and be turned on/off with systemctl.

No usernames are hardcoded -- the invoking user is resolved via
SUDO_USER / pwd at runtime.

Usage:
    python3 keylogger_installer.py                 # install everything
    python3 keylogger_installer.py --enable        # install + enable + start service
    python3 keylogger_installer.py --uninstall     # stop, disable, remove service + venv
    python3 keylogger_installer.py --uninstall --purge   # ...also delete data
    python3 keylogger_installer.py --status        # inspect current state (no root needed)
    python3 keylogger_installer.py --dry-run       # show what would happen (no root)
"""

import argparse
import os
import pwd
import shlex
import shutil
import sqlite3
import string
import json
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path

INSTALL_DIR = Path(__file__).resolve().parent
SERVICE_NAME = "dusky_keylogger"
SERVICE_FILE = Path("/etc/systemd/system") / f"{SERVICE_NAME}.service"
SERVICE_SRC = INSTALL_DIR / "systemd" / f"{SERVICE_NAME}.service"

C_RED = "\033[1;31m"
C_GREEN = "\033[1;32m"
C_YELLOW = "\033[1;33m"
C_CYAN = "\033[1;36m"
C_DIM = "\033[2m"
C_RESET = "\033[0m"

REQUIRED_PYTHON = (3, 15, 0)


def log(msg: str) -> None:
    print(msg)


def ok(msg: str) -> None:
    print(f"{C_GREEN}[ OK ]{C_RESET} {msg}")


def warn(msg: str) -> None:
    print(f"{C_YELLOW}[WARN]{C_RESET} {msg}")


def step(msg: str) -> None:
    print(f"{C_CYAN}[....]{C_RESET} {msg}")


def fail(msg: str) -> None:
    print(f"{C_RED}[FAIL]{C_RESET} {msg}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def run(
    cmd: list[str], check: bool = True, capture: bool = False
) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=capture, text=True)
    if check and proc.returncode != 0:
        fail(
            f"Command failed ({proc.returncode}): "
            f"{' '.join(shlex.quote(c) for c in cmd)}\n{proc.stderr}"
        )
    return proc


def original_user() -> tuple[str, str]:
    """Return (username, home_dir) of the user who invoked the installer."""
    record = pwd.getpwnam(os.environ["SUDO_USER"]) if os.environ.get("SUDO_USER") else pwd.getpwuid(os.getuid())
    return record.pw_name, record.pw_dir


def owner_spec(user: str) -> str:
    record = pwd.getpwnam(user)
    return f"{record.pw_uid}:{record.pw_gid}"


def ensure_user_directory(path: Path, user: str, mode: int = 0o700) -> None:
    """Own newly created parents, without changing shared existing parents."""
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    record = pwd.getpwnam(user)
    for directory in reversed(missing):
        directory.mkdir(mode=mode)
        os.chown(directory, record.pw_uid, record.pw_gid)
    path.chmod(mode)
    os.chown(path, record.pw_uid, record.pw_gid)


def venv_dir(home: str) -> Path:
    return Path(home) / "contained_apps" / "uv" / "dusky_key_logger"


def user_in_group(user: str, group: str) -> bool:
    proc = subprocess.run(["id", "-nG", user], capture_output=True, text=True)
    if proc.returncode != 0:
        return False
    return group in proc.stdout.split()


def system_python() -> str:
    return shutil.which("python3") or "python3"


def python_version(exe: str = sys.executable) -> tuple[int, int, int] | None:
    try:
        proc = subprocess.run(
            [
                exe,
                "-c",
                "import sys; print('.'.join(map(str, sys.version_info[:3])))",
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return None
        parts = proc.stdout.strip().split(".")
        if len(parts) != 3:
            return None
        return tuple(map(int, parts))
    except Exception:
        return None


def version_str(ver: tuple[int, int, int] | None) -> str:
    return ".".join(map(str, ver)) if ver else "unknown"


def has_uv() -> bool:
    return shutil.which("uv") is not None


# ---------------------------------------------------------------------------
# status (no root required)
# ---------------------------------------------------------------------------


def cmd_status(_args: argparse.Namespace) -> int:
    user, home = original_user()
    print(f"{C_CYAN}-- Dusky Keylogger status --{C_RESET}")
    ver = python_version()
    print(f"System Python:  {C_GREEN}{version_str(ver)}{C_RESET}")
    print(
        f"uv:             {'yes' if has_uv() else 'no (venv+pip fallback will be used)'}"
    )
    member = user_in_group(user, "input")
    print(
        f"Input group:    {C_GREEN if member else C_RED}"
        f"{'member' if member else 'NOT a member'}{C_RESET} "
        f"(user {user})"
    )
    print(f"Install dir:    {INSTALL_DIR}")
    venv = venv_dir(home)
    if venv.exists():
        vver = python_version(str(venv / "bin" / "python"))
        print(
            f"Venv:           {C_GREEN}present (Python {version_str(vver)}){C_RESET} ({venv})"
        )
    else:
        print(f"Venv:           {C_YELLOW}not created yet{C_RESET} ({venv})")
    if SERVICE_FILE.exists():
        print(f"Service file:   {C_GREEN}installed{C_RESET} ({SERVICE_FILE})")
    else:
        print(f"Service file:   {C_YELLOW}not installed{C_RESET}")

    # New canonical data dir first, then legacy migration source.
    new_data = Path(home) / ".config" / "dusky" / "settings" / "keylogger" / "data"
    legacy_data = Path(home) / ".local" / "share" / "dusky-keylogger"
    db = new_data / "keys.db"
    shown_legacy = False
    if not db.exists():
        legacy_db = legacy_data / "keys.db"
        if legacy_db.exists():
            db = legacy_db
            shown_legacy = True
    if db.exists():
        try:
            conn = sqlite3.connect(db.absolute().as_uri() + "?mode=ro", uri=True)
            try:
                total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            finally:
                conn.close()
            label = f"{db} -- {int(total):,} events"
            if shown_legacy:
                label += f"  {C_YELLOW}(legacy path; canonical is {new_data}){C_RESET}"
            print(f"Database:       {label}")
        except Exception as exc:
            print(f"Database:       {db} -- unreadable ({exc})")
    else:
        print(f"Database:       {C_YELLOW}no data yet{C_RESET}")

    # New canonical config (dusky/settings/keylogger) + legacy
    new_cfg = Path(home) / ".config" / "dusky" / "settings" / "keylogger" / "config.json"
    old_cfg = Path(home) / ".config" / "dusky-keylogger" / "config.json"
    if new_cfg.exists():
        print(f"Config (new):   {C_GREEN}present{C_RESET} ({new_cfg})")
    elif old_cfg.exists():
        print(f"Config (old):   {C_YELLOW}legacy present, will migrate to new on next run{C_RESET} ({old_cfg})")
    else:
        print(f"Config:         {C_YELLOW}not created yet (auto-created on fresh install){C_RESET}")

    for svc, flag in (("is-active", "active"), ("is-enabled", "enabled")):
        proc = subprocess.run(
            ["systemctl", svc, SERVICE_NAME], capture_output=True, text=True
        )
        print(f"Service {flag}: {C_GREEN}{proc.stdout.strip()}{C_RESET}")

    print("\nNext steps:")
    print("  python3 keylogger_installer.py --enable   -> build everything and start the daemon")
    print("  systemctl status dusky_keylogger")
    return 0


# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------


def cmd_dry_run(_args: argparse.Namespace) -> int:
    user, home = original_user()
    print(f"{C_CYAN}-- Dry run: what would happen --{C_RESET}")
    print("  1. Re-execute this script with sudo (auto-escalation).")
    if user_in_group(user, "input"):
        print(
            f"  2. {C_DIM}user {user} already in 'input' group -- nothing to do.{C_RESET}"
        )
    else:
        print(f"  2. Add user '{user}' to the 'input' group (usermod -aG input).")
    print(f"  3. Create venv at {venv_dir(home)} (uv using system Python, else venv+pip).")
    print("  4. Install package + deps from pyproject.toml (evdev, rich)" + (" using offline sources." if _args.offline else "."))
    print(f"  5. Install systemd service {SERVICE_FILE}.")
    if _args.enable:
        print("  6. systemctl enable --now dusky_keylogger")
    print("\nNo changes were made.")
    return 0


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------


def _ensure_root_for_uninstall(args: argparse.Namespace) -> None:
    if os.geteuid() == 0:
        return
    print(f"{C_CYAN}[ESCALATE]{C_RESET} Uninstall needs root -- re-executing with sudo...")
    sudo = shutil.which("sudo")
    if not sudo:
        fail("sudo not found -- run as root to uninstall.")
    argv = [sudo, sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]
    os.execv(sudo, argv)


def cmd_uninstall(args: argparse.Namespace) -> int:
    # Uninstall touches /etc/systemd/system and may need root even though
    # status/dry-run don't. Escalate if not already root.
    _ensure_root_for_uninstall(args)
    user, home = original_user()
    step("Stopping and disabling service...")
    stopped = run(["systemctl", "stop", SERVICE_NAME], check=False, capture=True)
    if stopped.returncode != 0:
        state = run(
            ["systemctl", "show", "--property=LoadState", "--value", SERVICE_NAME],
            check=False, capture=True,
        )
        if state.returncode != 0 or state.stdout.strip() != "not-found":
            fail(f"Could not stop {SERVICE_NAME}; uninstall aborted.\n{stopped.stderr}")
    with ExitStack() as locks:
        if args.purge:
            data_dir = Path(home) / ".local" / "share" / "dusky-keylogger"
            old_config = Path(home) / ".config" / "dusky-keylogger"
            new_config = Path(home) / ".config" / "dusky" / "settings" / "keylogger"
            from dusky_keylogger.storage import KeyStore
            record = pwd.getpwnam(user)
            try:
                data_paths = [p for p in (data_dir, new_config / "data") if p.exists()]
                for path in data_paths:
                    fd = locks.enter_context(KeyStore(path / "keys.db").collector_lock())
                    os.fchown(fd, record.pw_uid, record.pw_gid)
            except RuntimeError as exc:
                fail(f"Purge refused: {exc}")
        run(["systemctl", "disable", SERVICE_NAME], check=False)
        if SERVICE_FILE.exists():
            try:
                SERVICE_FILE.unlink()
            except OSError as exc:
                fail(f"Could not remove {SERVICE_FILE}: {exc}")
            run(["systemctl", "daemon-reload"])
            ok(f"Removed {SERVICE_FILE}")
        else:
            print(f"{C_DIM}Service file not present -- skipping.{C_RESET}")
        venv = venv_dir(home)
        if venv.exists():
            shutil.rmtree(venv)
            ok(f"Removed venv ({venv})")
        if args.purge:
            # Keep lock inodes and their parents in place. Removing them
            # would let a concurrent collector acquire a different lock.
            for path in data_paths:
                for child in path.iterdir():
                    if child.name != "keys.db.lock":
                        if child.is_dir() and not child.is_symlink():
                            shutil.rmtree(child)
                        else:
                            child.unlink()
                ok(f"Purged {path} (ownership lock retained)")
            if old_config.exists():
                shutil.rmtree(old_config)
                ok(f"Removed {old_config}")
            if new_config.exists():
                # A custom data directory may live beside config.json. Only
                # remove the known configuration file, not arbitrary stores.
                (new_config / "config.json").unlink(missing_ok=True)
                ok(f"Purged configuration in {new_config}")
    print(f"{C_GREEN}Done. The 'input' group membership was left untouched.{C_RESET}")
    return 0


# ---------------------------------------------------------------------------
# install (run as root)
# ---------------------------------------------------------------------------


def build_venv(user: str, home: str, *, offline: bool = False) -> str:
    venv = venv_dir(home)
    ensure_user_directory(venv.parent, user)
    step(f"Creating virtual environment at {venv}...")
    if not venv.exists():
        created = False
        if has_uv():
            proc = subprocess.run(
                ["uv", "venv", str(venv), "--python", system_python(), "--no-python-downloads"],
                capture_output=True,
                text=True,
            )
            created = proc.returncode == 0
            if not created:
                warn(f"uv venv using system Python failed:\n{proc.stderr.strip()}")
        if not created:
            ver = python_version(system_python())
            if not ver or ver < REQUIRED_PYTHON:
                fail(
                    f"CPython >= {'.'.join(map(str, REQUIRED_PYTHON))} required "
                    f"(found {ver}). Install python or uv."
                )
            run([system_python(), "-m", "venv", str(venv)])
    venv_py = venv / "bin" / "python"
    vver = python_version(str(venv_py))
    if not vver or vver < REQUIRED_PYTHON:
        fail(f"Venv Python is {vver}, need >= {REQUIRED_PYTHON}. "
             f"Recreate {venv} with the installed system Python, then rerun the installer.")
    ok(f"Venv ready: {venv} (Python {version_str(vver)})")

    step("Installing Dusky Keylogger + dependencies from pyproject.toml...")
    if has_uv():
        command = ["uv", "pip", "install", "--python", str(venv_py),
                   "--reinstall-package", "dusky-keylogger", str(INSTALL_DIR)]
        if offline:
            command.append("--offline")
        run(command)
    else:
        command = [str(venv_py), "-m", "pip", "install", str(INSTALL_DIR)]
        if offline:
            command.append("--no-index")
        run(command)
    ok("Package installed")

    run(["chown", "-R", owner_spec(user), str(venv)])
    return str(venv_py)


def install_service(venv_py: str, user: str, home: str) -> None:
    if not SERVICE_SRC.exists():
        fail(f"Service file missing: {SERVICE_SRC}")
    data_dir = f"{home}/.config/dusky/settings/keylogger/data"
    # Canonical config dir (writable under ProtectSystem=strict so the daemon
    # can auto-create config.json inside its sandbox).
    config_dir = f"{home}/.config/dusky/settings/keylogger"
    raw_content = SERVICE_SRC.read_text(encoding="utf-8")
    if "$" in raw_content:
        template = string.Template(raw_content)
        rendered = template.safe_substitute(
            USER=user,
            HOME=home,
            INSTALL_DIR=str(INSTALL_DIR),
            VENV_PYTHON=venv_py,
            DATA_DIR=data_dir,
            CONFIG_DIR=config_dir,
        )
    else:
        rendered = raw_content
    step(f"Installing {SERVICE_FILE}...")
    tmp = SERVICE_FILE.with_suffix(".service.tmp")
    tmp.write_text(rendered, encoding="utf-8")
    tmp.rename(SERVICE_FILE)
    os.chmod(SERVICE_FILE, 0o644)
    run(["systemctl", "daemon-reload"])
    ok("Service file installed")


def cmd_install(args: argparse.Namespace) -> int:
    if os.geteuid() != 0:
        print(
            f"{C_CYAN}[ESCALATE]{C_RESET} Not running as root -- re-executing with sudo..."
        )
        sudo = shutil.which("sudo")
        if not sudo:
            fail("sudo not found.")
        argv = [sudo, sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]
        os.execv(sudo, argv)

    user, home = original_user()
    print(f"{C_CYAN}-- Dusky Keylogger installer (root) --{C_RESET}")
    print(f"Installing for user: {C_GREEN}{user}{C_RESET} (home: {home})")

    ver = python_version(system_python())
    if not ver or ver < REQUIRED_PYTHON:
        fail(f"System Python >= {version_str(REQUIRED_PYTHON)} required (found {version_str(ver)}).")
    ok(f"Python {version_str(ver)}")

    if user_in_group(user, "input"):
        ok(f"User '{user}' is already in the 'input' group")
    else:
        step(f"Adding user '{user}' to the 'input' group...")
        run(["usermod", "-aG", "input", user])
        warn(f"User '{user}' added to 'input'. Foreground use needs logout/login; the system service receives the group immediately.")

    new_config = Path(home) / ".config/dusky/settings/keylogger"
    new_data_dir = new_config / "data"
    ensure_user_directory(new_data_dir, user)
    # Repair existing application-owned files, using the user's real primary GID.
    run(["chown", "-R", owner_spec(user), str(new_config)])
    from dusky_keylogger.daemon import DEFAULT_CONFIG
    cfg_file = new_config / "config.json"
    old_cfg = Path(home) / ".config/dusky-keylogger/config.json"
    if not cfg_file.exists():
        defaults = dict(DEFAULT_CONFIG)
        if old_cfg.exists():
            try:
                loaded = json.loads(old_cfg.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                fail(f"Could not read legacy config {old_cfg}: {exc}")
            if not isinstance(loaded, dict):
                fail(f"Legacy config is not a JSON object: {old_cfg}")
            defaults.update(loaded)
        fd = os.open(cfg_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(defaults, fh, indent=2)
            fh.write("\n")
        record = pwd.getpwnam(user)
        os.chown(cfg_file, record.pw_uid, record.pw_gid)

    active = run(["systemctl", "is-active", SERVICE_NAME], check=False, capture=True)
    was_active = active.returncode == 0
    if was_active:
        step("Stopping the running service before updating its package and unit...")
        run(["systemctl", "stop", SERVICE_NAME])
    venv_py = build_venv(user, home, offline=args.offline)
    install_service(venv_py, user, home)

    if args.enable:
        step("Enabling and starting service...")
        run(["systemctl", "enable", "--now", SERVICE_NAME])
        ok("Service enabled and started")
    elif was_active:
        run(["systemctl", "start", SERVICE_NAME])
        ok("Previously running service restarted with the updated package")

    print(f"\n{C_GREEN}Installation complete!{C_RESET}")
    print("  Control the daemon:")
    print(
        f"    sudo systemctl {'restart' if args.enable else 'start'} {SERVICE_NAME}"
    )
    print(f"    sudo systemctl stop {SERVICE_NAME}")
    print(
        f"    sudo systemctl {'disable' if args.enable else 'enable --now'} {SERVICE_NAME}"
    )
    print("  View analytics:")
    print(f"    {venv_dir(home) / 'bin' / 'python'} -m dusky_keylogger dashboard")
    print(
        f"    {venv_dir(home) / 'bin' / 'python'} -m dusky_keylogger stats --period week"
    )
    return 0


# ---------------------------------------------------------------------------
# entry
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install Dusky Keylogger")
    parser.add_argument("--offline", action="store_true", help="Install from uv cache or pip local sources without network access")
    parser.add_argument(
        "--enable", action="store_true", help="Also enable + start the systemd service"
    )
    parser.add_argument(
        "--uninstall", action="store_true", help="Remove service + venv"
    )
    parser.add_argument(
        "--purge", action="store_true", help="With --uninstall: also delete data"
    )
    parser.add_argument(
        "--status", action="store_true", help="Show current state (no root needed)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show the plan without changing anything"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.status:
        return cmd_status(args)
    if args.dry_run:
        return cmd_dry_run(args)
    if args.uninstall:
        return cmd_uninstall(args)
    return cmd_install(args)


if __name__ == "__main__":
    sys.exit(main())
