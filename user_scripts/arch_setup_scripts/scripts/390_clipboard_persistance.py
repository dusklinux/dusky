#!/usr/bin/env python3
#d: Keep the clipboard persistent across reboots
"""Switch the managed clipboard backend without reconnecting its watchers."""

import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

os.umask(0o077)
HOME = Path.home()
STATE_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "dusky/settings"
STATE_FILE = STATE_DIR / "clipboard_persistance"
DB_ENV_FILE = STATE_DIR / "cliphist_db_env"
LOCK_FILE = STATE_DIR / ".clipboard_backend.lock"
DAEMON = Path(__file__).resolve().parents[2] / "clipboard/dusky_clipboard_daemon.sh"
SERVICE = "dusky_clipboard.service"
QUIET = False


class SwitchError(RuntimeError):
    pass


def say(message):
    if not QUIET:
        print(message)


def warn(message):
    print(f"Warning: {message}", file=sys.stderr)


def checked_path(value):
    path = Path(value)
    # These paths also appear in systemd EnvironmentFile and the menu's parser.
    if not path.is_absolute() or any(c in str(path) for c in '\\"\r\n\x00'):
        raise SwitchError(f"Unsupported clipboard path: {str(path)!r}")
    return path


def disk_db():
    return checked_path(Path(os.environ.get("XDG_CACHE_HOME") or HOME / ".cache") / "cliphist/db")


def ram_db():
    return checked_path(Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / "cliphist.db")


def env_for(db):
    env = os.environ.copy()
    env["CLIPHIST_DB_PATH"] = str(db)
    env["CLIPHIST_PREVIEW_WIDTH"] = "1"
    return env


def run(argv, *, env=None, timeout=5):
    try:
        result = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SwitchError(f"{argv[0]} failed: {exc}") from exc
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip()[:600]
        raise SwitchError(f"{' '.join(map(str, argv[:3]))} exited {result.returncode}: {detail}")
    return result


def regular_file(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise SwitchError(f"Expected an owned regular file: {path}")
    return True


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".clipboard-", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        sync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)


@contextlib.contextmanager
def locked(path, operation=fcntl.LOCK_EX, timeout=15, *, create=True):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = (os.O_RDWR | os.O_CREAT) if create else os.O_RDONLY
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise SwitchError(f"Unsafe lock file: {path}")
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, operation | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise SwitchError(f"Timed out waiting for clipboard lock: {path}") from None
                time.sleep(0.02)
        yield fd
    finally:
        os.close(fd)


def current_db():
    if not regular_file(DB_ENV_FILE):
        return disk_db()
    value = None
    for line in DB_ENV_FILE.read_text().splitlines():
        match = re.fullmatch(r'\s*(?:export\s+)?CLIPHIST_DB_PATH\s*=\s*(.*?)\s*', line)
        if match:
            text = match[1]
            if text.startswith(('"', "'")):
                quote = text[0]
                end = text.find(quote, 1)
                if end < 0:
                    raise SwitchError("Malformed clipboard database configuration")
                value = text[1:end]
            else:
                value = text.split()[0] if text else None
    if not value:
        raise SwitchError("No valid CLIPHIST_DB_PATH in clipboard configuration")
    return checked_path(value)


def validate_db(db):
    if regular_file(db):
        # Ignore cliphist's personal config for this read-only integrity check.
        run(["cliphist", "-config-path", "/dev/null", "-db-path", str(db),
             "-preview-width", "1", "list"],
            env=env_for(db) | {"CLIPHIST_CONFIG_PATH": "/dev/null"}, timeout=5)


def service_state():
    output = run(["systemctl", "--user", "show", SERVICE,
                  "--property=LoadState,ActiveState,MainPID"]).stdout.decode()
    values = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    if values.get("LoadState") != "loaded":
        raise SwitchError(f"{SERVICE} is not installed")
    if values.get("ActiveState") in {"inactive", "failed"} and values.get("MainPID") == "0":
        return None
    if values.get("ActiveState") != "active":
        raise SwitchError(f"{SERVICE} is changing state; try again when it settles")
    try:
        pid = int(values["MainPID"])
        children = Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
        watchers = {}
        persistence = []
        for child in children:
            args = Path(f"/proc/{child}/cmdline").read_bytes().rstrip(b"\0").split(b"\0")
            if not args:
                continue
            name = Path(os.fsdecode(args[0])).name
            if name == "wl-paste" and b"--type" in args:
                kind = os.fsdecode(args[args.index(b"--type") + 1])
                expected = [b"--watch", os.fsencode(DAEMON), b"--store"]
                if args[-3:] != expected:
                    raise SwitchError(
                        "The running clipboard daemon needs a one-time update. Run "
                        f"'systemctl --user restart {SERVICE}' before switching storage.")
                if kind in watchers:
                    raise SwitchError(f"Duplicate managed {kind} watchers")
                watchers[kind] = int(child)
            elif name == "wl-clip-persist":
                persistence.append(int(child))
        if set(watchers) != {"text", "image"} or len(persistence) != 1:
            raise SwitchError("Clipboard service does not have its expected three children")
        return pid, watchers["text"], watchers["image"], persistence[0]
    except (OSError, ValueError, KeyError, IndexError) as exc:
        raise SwitchError("Clipboard service changed while checking its children; try again") from exc


def migrate_snapshot(source, destination):
    # Never overwrite an existing destination, including an empty Bolt database.
    if destination.exists() or destination.is_symlink():
        raise SwitchError("Migration refused: destination already exists. Use a normal switch to preserve both histories.")
    if not regular_file(source):
        raise SwitchError(f"Migration source does not exist: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".clipboard-migrate-", dir=destination.parent)
    temp = Path(name)
    try:
        # Bolt uses flock too. Holding its shared file lock prevents a torn copy
        # even if an external cliphist writer does not use our backend lock.
        with os.fdopen(fd, "wb") as output, locked(source, fcntl.LOCK_SH, timeout=5, create=False) as source_fd:
            with os.fdopen(os.dup(source_fd), "rb") as input_file:
                shutil.copyfileobj(input_file, output)
            output.flush()
            os.fsync(output.fileno())
        validate_db(temp)
        os.link(temp, destination)  # Atomic publication, fails if someone created it.
        sync_directory(destination.parent)
    finally:
        temp.unlink(missing_ok=True)


def update_launch_environments(db):
    # Supplementary propagation only; the daemon and menu read the authoritative
    # config for every operation. Failure here cannot roll back a completed switch.
    commands = []
    if shutil.which("dbus-update-activation-environment"):
        commands.append(["dbus-update-activation-environment", "CLIPHIST_DB_PATH"])
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        commands.append(["hyprctl", "eval", f"hl.env('CLIPHIST_DB_PATH', {json.dumps(str(db), ensure_ascii=False)})"])
    for command in commands:
        try:
            run(command, env=env_for(db), timeout=3)
        except SwitchError as exc:
            warn(f"Storage switched, but a launch environment was not updated: {exc}")


def switch(mode, migrate=False):
    checked_path(STATE_DIR)
    target = ram_db() if mode == "ram" else disk_db()
    desired_state = b"false\n" if mode == "ram" else b"true\n"
    desired_env = f'CLIPHIST_DB_PATH="{target}"\n'.encode()
    with locked(LOCK_FILE):
        before = service_state()
        previous = current_db()
        old_files = {p: p.read_bytes() if regular_file(p) else None for p in (DB_ENV_FILE, STATE_FILE)}
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        validate_db(target)
        if migrate:
            source = disk_db() if mode == "ram" else ram_db()
            if source == target:
                raise SwitchError("Migration source and destination are the same path")
            migrate_snapshot(source, target)
        if regular_file(target):
            target.chmod(0o600)
        try:
            # Readers choose either the complete old path or complete new path.
            # Store callbacks hold the same lock shared until their DB commit.
            write_atomic(DB_ENV_FILE, desired_env)
            write_atomic(STATE_FILE, desired_state)
            run(["systemctl", "--user", "set-environment", f"CLIPHIST_DB_PATH={target}"])
            if service_state() != before:
                raise SwitchError("Clipboard service changed during the switch")
        except (OSError, SwitchError, KeyboardInterrupt) as exc:
            failures = []
            for path, data in old_files.items():
                try:
                    if data is None:
                        path.unlink(missing_ok=True)
                        sync_directory(path.parent)
                    else:
                        write_atomic(path, data)
                except OSError as restore_error:
                    failures.append(str(restore_error))
            try:
                run(["systemctl", "--user", "set-environment", f"CLIPHIST_DB_PATH={previous}"])
            except SwitchError as restore_error:
                failures.append(str(restore_error))
            if failures:
                raise SwitchError(f"Switch failed ({exc}); rollback needs attention: {'; '.join(failures)}") from exc
            raise SwitchError(f"Switch failed; previous configuration restored: {exc}") from exc
        # Keep supplementary propagation serialized as well, so two concurrent
        # callers cannot publish their session environments in reverse order.
        update_launch_environments(target)
    suffix = "" if before else " (clipboard service is stopped; applies when it starts)"
    say(f"Clipboard storage: {'RAM' if mode == 'ram' else 'disk'} → {target}{suffix}")
    return target


def main():
    global QUIET
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--ram", action="store_true")
    group.add_argument("--disk", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--migrate", action="store_true", help="copy the other history only if the destination does not exist")
    args = parser.parse_args()
    QUIET = args.quiet
    if os.geteuid() == 0:
        parser.error("Run as your desktop user, not root")
    mode = "ram" if args.ram else "disk" if args.disk else None
    if mode is None:
        if not sys.stdin.isatty():
            parser.error("Use --ram or --disk outside an interactive terminal")
        print("Clipboard storage\n1) RAM — lost on reboot\n2) Disk — retained across reboots")
        try:
            choice = input("Select [1/2] (default 1): ").strip()
        except (EOFError, KeyboardInterrupt):
            return 130
        if choice not in {"", "1", "2"}:
            parser.error("Select 1 or 2")
        mode = "disk" if choice == "2" else "ram"
    try:
        switch(mode, args.migrate)
    except (OSError, ValueError, SwitchError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
