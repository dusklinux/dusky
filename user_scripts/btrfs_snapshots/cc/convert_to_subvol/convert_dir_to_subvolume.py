#!/usr/bin/env python3
"""Convert directories to independently mounted, top-level Btrfs subvolumes.

Stop applications writing to the target before conversion or undo. Copying is
not a snapshot: open file descriptors and concurrent writers cannot be migrated.
The shared Dusky lock serializes cooperating filesystem operations only.
Handled failures restore the directory and fstab; failed rollback retains recovery
copies. SIGKILL and power loss require manual recovery from those copies.
"""

import argparse
import fcntl
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

BTRFS_FS_TREE_OBJECTID = 5

RUN_DIR = Path("/run/dusky")
MNT_ROOT = RUN_DIR / "mnt"
LOCK_PATH = RUN_DIR / "dusky.lock"
FSTAB_PATH = Path("/etc/fstab")

SAFE_NAME_RE = re.compile(r"\A@[A-Za-z0-9_.-]{1,180}\Z")
MOUNT_DIR_RE = re.compile(r"\Atop_(?P<pid>\d+)_(?P<tag>[A-Za-z0-9_]+)\Z")
UUID_RE = re.compile(r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")

# Transient & system subvolume protection
TRANSIENT_PATTERNS = ("_to_delete_", "_dusky_new_", ".tmp_send_", ".dusky_probe_")
PROTECTED_SUBVOLUMES = {"@", "@home", "@snapshots", "@home_snapshots", "@var_log", "@var_cache", "@var_tmp", "@swap"}

_ENV_PASSTHROUGH = ("TERM", "TERMINFO", "COLORTERM", "TZ")
SUBPROCESS_ENV = frozendict({
    **{k: os.environ[k] for k in _ENV_PASSTHROUGH if k in os.environ},
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin",
    "LC_ALL": "C.UTF-8",
    "LANG": "C.UTF-8",
})


class ConversionError(RuntimeError):
    pass


def die(msg: str, exit_code: int = 1) -> None:
    print(f"\033[1;31m[FATAL]\033[0m {msg}", file=sys.stderr)
    sys.exit(exit_code)


def info(msg: str) -> None:
    print(f"\033[1;32m[INFO]\033[0m {msg}")


def warn(msg: str) -> None:
    print(f"\033[1;33m[WARN]\033[0m {msg}", file=sys.stderr)


def good(msg: str) -> None:
    print(f"\033[1;32m{msg}\033[0m")


def run(*argv: str, check: bool = True, timeout: float | None = 300.0) -> subprocess.CompletedProcess[str]:
    cmd = [str(a) for a in argv]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            env=SUBPROCESS_ENV,
            timeout=timeout,
            check=False,
        )
    except OSError as exc:
        raise ConversionError(f"Could not execute {cmd[0]} ({exc})") from exc
    except subprocess.TimeoutExpired as exc:
        raise ConversionError(f"Timed out after {timeout}s: {shlex.join(cmd)}") from exc

    if check and proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or "unknown error"
        raise ConversionError(f"Command failed ({proc.returncode}): {shlex.join(cmd)}\n    {err}")
    return proc


def ensure_root() -> None:
    if os.geteuid() != 0:
        if shutil.which("sudo", path=SUBPROCESS_ENV["PATH"]) is None:
            die("Root privileges required and sudo is not installed.")
        argv = ["sudo", "--", sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]
        os.execvp("sudo", argv)


# =============================================================================
# LOCKING & SIGNALS
# =============================================================================
_LOCK_FD: int | None = None
_LOCK_DEPTH = 0


@contextmanager
def dusky_lock(*, wait: bool = True) -> Iterator[None]:
    global _LOCK_FD, _LOCK_DEPTH
    if _LOCK_DEPTH > 0:
        _LOCK_DEPTH += 1
        try:
            yield
        finally:
            _LOCK_DEPTH -= 1
        return

    try:
        RUN_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(RUN_DIR, 0o700)
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    except OSError as exc:
        die(f"Cannot create lock {LOCK_PATH}: {exc}")

    acquired = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if not wait:
                die("Another Dusky operation holds the lock. Refusing to queue.")
            holder = ""
            with suppress(OSError):
                holder = os.pread(fd, 64, 0).decode(errors="replace").strip()
            warn(f"Waiting for Dusky lock (held by pid {holder or '?'})...")
            fcntl.flock(fd, fcntl.LOCK_EX)
        acquired = True
        with suppress(OSError):
            os.ftruncate(fd, 0)
            os.pwrite(fd, f"{os.getpid()}\n".encode(), 0)
        _LOCK_FD, _LOCK_DEPTH = fd, 1
        yield
    finally:
        if acquired:
            _LOCK_DEPTH = max(0, _LOCK_DEPTH - 1)
        if _LOCK_DEPTH == 0:
            _LOCK_FD = None
            with suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
        with suppress(OSError):
            if _LOCK_FD is None:
                os.close(fd)


@contextmanager
def critical_section() -> Iterator[None]:
    blocked = {signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT, signal.SIGPIPE}
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, blocked)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def fsync_path(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_file_durable(path: Path, content: bytes) -> None:
    """Replace a file atomically, preserving its owner and permission bits."""
    original = path.stat()
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.dusky-", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchown(stream.fileno(), original.st_uid, original.st_gid)
            os.fchmod(stream.fileno(), original.st_mode & 0o7777)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        fsync_path(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


# =============================================================================
# MOUNT & SUBVOLUME UTILITIES
# =============================================================================
def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def is_mountpoint(path: Path) -> bool:
    proc = run("mountpoint", "--quiet", "--", str(path), check=False)
    if proc.returncode not in (0, 32):
        raise ConversionError(f"Cannot inspect mountpoint {path}: {proc.stderr.strip()}")
    return proc.returncode == 0


def path_is_subvolume(path: Path) -> bool:
    return run("btrfs", "subvolume", "show", "--", str(path), check=False).returncode == 0


def get_subvolume_id(path: Path) -> int:
    value = run("btrfs", "inspect-internal", "rootid", "--", str(path)).stdout.strip()
    try:
        return int(value)
    except ValueError as exc:
        raise ConversionError(f"Invalid subvolume ID for {path}: {value!r}") from exc


def sweep_stale_mounts() -> None:
    if not MNT_ROOT.is_dir():
        return
    for child in sorted(MNT_ROOT.iterdir()):
        match = MOUNT_DIR_RE.fullmatch(child.name)
        if not child.is_dir() or match is None:
            continue
        if _pid_alive(int(match.group("pid"))):
            continue
        if is_mountpoint(child):
            if run("umount", "--", str(child), check=False, timeout=None).returncode != 0:
                warn(f"Stale mount remains busy: {child}")
                continue
        with suppress(OSError):
            child.rmdir()


def _ensure_private_mnt_root() -> None:
    MNT_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(MNT_ROOT, 0o700)
    if not is_mountpoint(MNT_ROOT):
        run("mount", "--bind", str(MNT_ROOT), str(MNT_ROOT), timeout=None)
    run("mount", "--make-rprivate", str(MNT_ROOT), timeout=None)


@contextmanager
def top_level(fs_uuid: str, base_opts: str = "") -> Iterator[Path]:
    if not UUID_RE.fullmatch(fs_uuid):
        raise ConversionError(f"Malformed filesystem UUID {fs_uuid!r}.")

    _ensure_private_mnt_root()
    sweep_stale_mounts()

    mnt = Path(tempfile.mkdtemp(prefix=f"top_{os.getpid()}_", dir=str(MNT_ROOT)))
    opts = "subvolid=5,nodev,nosuid,noexec,noatime"
    if "degraded" in base_opts.split(","):
        opts += ",degraded"
    src = f"UUID={fs_uuid}"

    try:
        run("mount", "--types", "btrfs", "--options", opts, "--", src, str(mnt), timeout=None)
        seen_uuid = get_mount_info(mnt).get("uuid", "")
        if seen_uuid.lower() != fs_uuid.lower():
            raise ConversionError(f"Mounted UUID={seen_uuid}, expected UUID={fs_uuid}.")
        sid = get_subvolume_id(mnt)
        if sid != BTRFS_FS_TREE_OBJECTID:
            raise ConversionError(f"{mnt} reports subvolume id {sid}, expected 5.")
        yield mnt
    finally:
        with critical_section():
            try:
                if is_mountpoint(mnt):
                    for attempt in range(5):
                        if run("umount", "--", str(mnt), check=False, timeout=None).returncode == 0:
                            break
                        time.sleep(0.2 * (attempt + 1))
                    else:
                        warn(f"Temporary mount remains busy; unmount it manually: {mnt}")
                mnt.rmdir()
            except (OSError, ConversionError) as exc:
                warn(f"Temporary mount cleanup incomplete at {mnt}: {exc}")


def mount_records(*args: str) -> list[dict[str, str]]:
    proc = run("findmnt", "--json", "--list", "--output",
               "TARGET,FSTYPE,OPTIONS,UUID,FSROOT", *args)
    try:
        records = json.loads(proc.stdout)["filesystems"]
        if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
            raise ValueError("invalid filesystem records")
        return [{k: str(v or "") for k, v in record.items()} for record in records]
    except (ValueError, KeyError, TypeError) as exc:
        raise ConversionError("Could not parse findmnt output") from exc


def get_mount_info(target: Path) -> dict[str, str]:
    records = mount_records("--target", str(target))
    if len(records) != 1:
        raise ConversionError(f"Expected one filesystem for {target}, got {len(records)}")
    return records[0]


def clean_mount_opts(opts: str) -> str:
    return ",".join(opt for part in opts.split(",")
                    if (opt := part.strip()) and opt not in {"ro", "rw"}
                    and not opt.startswith(("subvol=", "subvolid=")))


def derive_subvol_name(target_path: Path) -> str:
    return "@" + re.sub(r"[^A-Za-z0-9_.-]", "_", "_".join(target_path.parts[1:]))


def fstab_escape(value: str) -> str:
    return (value.replace("\\", r"\134").replace(" ", r"\040")
            .replace("\t", r"\011").replace("\n", r"\012"))


def fstab_unescape(value: str) -> str:
    return re.sub(r"\\(040|011|012|134)", lambda m: chr(int(m[1], 8)), value)


def fstab_content(original: bytes, target: Path, entry: str | None) -> bytes:
    lines = []
    inserted = False
    # fstab separates fields with ASCII spaces/tabs; Unicode filename characters
    # and unrelated lines must survive an add/remove cycle unchanged.
    for raw_line in original.splitlines(keepends=True):
        line = raw_line.decode("utf-8", errors="surrogateescape")
        fields = re.split(r"[ \t]+", line.strip(" \t\r\n"))
        if not line.lstrip(" \t").startswith("#") and len(fields) >= 2:
            mount_target = fstab_unescape(fields[1]).rstrip("/") or "/"
            # Insert a new parent before child entries, as mount -a uses file order.
            if (entry is not None and not inserted
                    and (mount_target == str(target) or mount_target.startswith(f"{target}/"))):
                lines.append(entry + "\n")
                inserted = True
            if mount_target == str(target):
                continue
        lines.append(line)
    content = "".join(lines)
    if entry is not None and not inserted:
        if content and not content.endswith("\n"):
            content += "\n"
        content += entry + "\n"
    return content.encode("utf-8", errors="surrogateescape")


@contextmanager
def fstab_transaction(target: Path, entry: str | None) -> Iterator[None]:
    """Keep a durable backup; roll back even if daemon-reload or activation fails."""
    original = FSTAB_PATH.read_bytes()
    updated = fstab_content(original, target, entry)
    if updated == original:
        yield
        return
    fd, name = tempfile.mkstemp(prefix=".fstab.check-", dir=FSTAB_PATH.parent)
    candidate = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(updated)
        proc = run("findmnt", "--verify", "--tab-file", str(candidate), check=False)
        if proc.returncode:
            raise ConversionError(f"Generated fstab failed validation:\n{proc.stdout}{proc.stderr}")
    finally:
        candidate.unlink(missing_ok=True)

    fd, name = tempfile.mkstemp(prefix="fstab.bak.", dir=FSTAB_PATH.parent)
    os.close(fd)
    backup = Path(name)
    shutil.copy2(FSTAB_PATH, backup)
    fd = os.open(backup, os.O_RDONLY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    fsync_path(backup.parent)
    info(f"Fstab backup: {backup}")
    try:
        write_file_durable(FSTAB_PATH, updated)
        run("systemctl", "daemon-reload")
        yield
    except BaseException as exc:
        try:
            write_file_durable(FSTAB_PATH, original)
            run("systemctl", "daemon-reload")
        except BaseException as rollback_error:
            raise ConversionError(
                f"{exc}; fstab rollback failed: {rollback_error}. Restore {backup} manually."
            ) from exc
        raise


def validate_name(name: str) -> None:
    if not SAFE_NAME_RE.fullmatch(name):
        raise ConversionError(f"Invalid top-level name {name!r}; use @ plus 1–180 ASCII letters, digits, _, . or -.")
    if name in PROTECTED_SUBVOLUMES or any(pat in name for pat in TRANSIENT_PATTERNS):
        raise ConversionError(f"Reserved Dusky subvolume name: {name!r}")


def inspect_target(target: Path, *, undo: bool) -> dict[str, str]:
    if not target.is_dir():
        raise ConversionError(f"Target is not an existing directory: {target}")
    mounted = is_mountpoint(target)
    if mounted != undo:
        raise ConversionError(f"Target must {'be' if undo else 'not be'} a mountpoint: {target}")
    data = get_mount_info(target)
    if data["fstype"] != "btrfs" or not UUID_RE.fullmatch(data["uuid"]):
        raise ConversionError(f"Target must be on a Btrfs filesystem with a valid UUID: {target}")
    if not undo and "ro" in data["options"].split(","):
        raise ConversionError(f"Target filesystem is read-only: {target}")
    if path_is_subvolume(target) != undo:
        raise ConversionError(f"Target must {'be' if undo else 'not be'} a subvolume root: {target}")
    for record in mount_records():
        if Path(record["target"]).is_relative_to(target) and record["target"] != str(target):
            raise ConversionError(f"Unmount nested mount {record['target']} before proceeding.")
    # Subvolume roots have inode 256; do not follow symlinked directories.
    def walk_error(exc: OSError) -> None:
        raise exc
    for root, dirs, _ in os.walk(target, onerror=walk_error):
        for name in dirs:
            child = Path(root) / name
            if child.lstat().st_ino == 256 and path_is_subvolume(child):
                raise ConversionError(f"Nested subvolume must be handled separately: {child}")
    return data


def copy_directory(source: Path, destination: Path) -> None:
    # Copy the directory itself to preserve root metadata and avoid ARG_MAX.
    # Unlike --archive alone, explicit xattr preservation makes failures fatal.
    run("cp", "--archive", "--preserve=xattr", "--reflink=auto", "--no-target-directory",
        "--", str(source), str(destination), timeout=None)
    run("btrfs", "filesystem", "sync", "--", str(destination), timeout=None)


def temporary_sibling(target: Path, tag: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=f".dusky-{tag}-", dir=target.parent))


def verify_mount(target: Path, fs_uuid: str, name: str, sid: int) -> None:
    data = get_mount_info(target)
    if (data["target"] != str(target) or data["fstype"] != "btrfs"
            or data["uuid"].lower() != fs_uuid.lower() or data["fsroot"] != f"/{name}"
            or not path_is_subvolume(target) or get_subvolume_id(target) != sid):
        raise ConversionError(f"Mounted filesystem/subvolume does not match {name} (ID {sid}) at {target}")


def remove_copy(path: Path, *, subvolume: bool = False) -> None:
    if subvolume:
        run("btrfs", "subvolume", "delete", "--commit-after", "--", str(path), timeout=None)
    else:
        shutil.rmtree(path)


def convert_directory(target_path: Path, custom_subvol_name: str | None = None) -> None:
    target = target_path.resolve(strict=True)
    name = custom_subvol_name if custom_subvol_name is not None else derive_subvol_name(target)
    validate_name(name)
    with dusky_lock():
        data = inspect_target(target, undo=False)
        fs_uuid = data["uuid"]
        warn("Stop all writers to this directory before proceeding; copying is not a snapshot.")
        with top_level(fs_uuid, data["options"]) as top:
            subvol = top / name
            if subvol.exists() or subvol.is_symlink():
                raise ConversionError(f"Top-level path already exists: {name}")
            backup = None
            created = False
            mount_dir_created = False
            committed = False
            try:
                with critical_section():
                    run("btrfs", "subvolume", "create", "--", str(subvol), timeout=None)
                    created = True
                info(f"Copying {target} into {name}...")
                copy_directory(target, subvol)
                sid = get_subvolume_id(subvol)
                opts = clean_mount_opts(data["options"])
                entry = (f"UUID={fs_uuid} {fstab_escape(str(target))} btrfs "
                         f"{opts + ',' if opts else ''}subvol=/{name} 0 0")
                with critical_section():
                    try:
                        reserved = temporary_sibling(target, "original")
                        try:
                            target.rename(reserved)
                        except BaseException:
                            reserved.rmdir()
                            raise
                        backup = reserved
                        info(f"Original directory recovery copy: {backup}")
                        target.mkdir(mode=0o755)
                        mount_dir_created = True
                        fsync_path(target.parent)
                        with fstab_transaction(target, entry):
                            run("mount", "--", str(target), timeout=None)
                            verify_mount(target, fs_uuid, name, sid)
                        committed = True
                    except BaseException:
                        # Never recurse into a mount or delete its files on rollback.
                        if backup is not None:
                            if is_mountpoint(target):
                                run("umount", "--", str(target), timeout=None)
                            if mount_dir_created:
                                target.rmdir()
                            backup.rename(target)
                            backup = None
                            fsync_path(target.parent)
                        raise
            except BaseException as exc:
                with critical_section():
                    if committed:
                        warn(f"Conversion committed before interruption; original backup retained at {backup}.")
                        raise
                    if backup is not None:
                        raise ConversionError(
                            f"{exc}; directory rollback incomplete. Original retained at {backup}; "
                            f"subvolume {name} retained."
                        ) from exc
                    if created:
                        try:
                            remove_copy(subvol, subvolume=True)
                        except BaseException as cleanup_error:
                            raise ConversionError(f"{exc}; remove leftover subvolume {name}: {cleanup_error}") from exc
                raise
            # Activation has committed. Cleanup failure must not undo a working mount.
            try:
                remove_copy(backup)
                fsync_path(target.parent)
            except OSError as exc:
                warn(f"Conversion committed; original backup cleanup failed at {backup}: {exc}")
    good(f"[+] SUCCESS: {target} is mounted as top-level subvolume {name}.")


def revert_directory(target_path: Path) -> None:
    target = target_path.resolve(strict=True)
    with dusky_lock():
        data = inspect_target(target, undo=True)
        name = data["fsroot"].removeprefix("/")
        validate_name(name)
        fs_uuid = data["uuid"]
        sid = get_subvolume_id(target)
        if sid == BTRFS_FS_TREE_OBJECTID:
            raise ConversionError("Cannot undo filesystem tree root (ID 5).")
        for record in mount_records():
            if (record["uuid"].lower() == fs_uuid.lower() and record["target"] != str(target)
                    and (record["fsroot"] == f"/{name}" or record["fsroot"].startswith(f"/{name}/"))):
                raise ConversionError(f"Subvolume also mounted at {record['target']}; unmount it first.")
        warn("Stop all writers to this directory before proceeding; copying is not a snapshot.")
        with top_level(fs_uuid, data["options"]) as top:
            subvol = top / name
            if not path_is_subvolume(subvol) or get_subvolume_id(subvol) != sid:
                raise ConversionError(f"Top-level subvolume {name} does not match the mounted ID {sid}.")
            default = run("btrfs", "subvolume", "get-default", str(top)).stdout
            default_id = re.search(r"\bID\s+(\d+)\b", default)
            if default_id is None:
                raise ConversionError(f"Cannot parse the filesystem's default subvolume: {default!r}")
            if int(default_id[1]) == sid:
                raise ConversionError(f"Cannot undo the filesystem's default subvolume {name}.")
            staging = temporary_sibling(target, "undo")
            underlying = None
            unmounted = False
            installed = False
            committed = False
            try:
                copy_directory(target, staging)
                with critical_section():
                    try:
                        run("umount", "--", str(target), timeout=None)
                        unmounted = True
                        underlying = temporary_sibling(target, "mountpoint")
                        try:
                            target.rename(underlying)
                        except BaseException:
                            underlying.rmdir()
                            underlying = None
                            raise
                        staging.rename(target)
                        installed = True
                        fsync_path(target.parent)
                        with fstab_transaction(target, None):
                            if is_mountpoint(target) or path_is_subvolume(target):
                                raise ConversionError(f"Undo did not produce a regular directory: {target}")
                        committed = True
                    except BaseException:
                        if installed:
                            target.rename(staging)
                            installed = False
                        if underlying is not None:
                            underlying.rename(target)
                            underlying = None
                        if unmounted:
                            # Explicit source/options also work if fstab had no entry.
                            run("mount", "--types", "btrfs", "--options", data["options"],
                                "--", f"UUID={fs_uuid}", str(target), timeout=None)
                            verify_mount(target, fs_uuid, name, sid)
                            unmounted = False
                        fsync_path(target.parent)
                        raise
            except BaseException as exc:
                if committed:
                    warn(f"Undo committed before interruption; old subvolume {name} and mountpoint backup {underlying} retained.")
                    raise
                if unmounted or installed or underlying is not None:
                    raise ConversionError(
                        f"{exc}; undo rollback incomplete. Data retained in subvolume {name}, "
                        f"buffer {staging}, mountpoint backup {underlying}."
                    ) from exc
                try:
                    remove_copy(staging)
                except OSError as cleanup_error:
                    warn(f"Undo buffer retained at {staging}: {cleanup_error}")
                raise
            try:
                remove_copy(subvol, subvolume=True)
            except ConversionError as exc:
                warn(f"Undo committed; old subvolume {name} retained: {exc}")
            try:
                underlying.rmdir()  # Preserve any previously hidden mountpoint data.
                fsync_path(target.parent)
            except OSError as exc:
                warn(f"Old mountpoint contents retained at {underlying}: {exc}")
    good(f"[+] UNDO COMPLETE: {target} is now a regular directory.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a directory to a mounted top-level Btrfs subvolume, or undo it.",
        epilog="Stop all applications writing to the directory first. Power loss/SIGKILL require manual recovery.",
        allow_abbrev=False,
    )
    parser.add_argument("path", type=Path, help="Directory path to convert or undo")
    parser.add_argument("-n", "--name", help="Custom top-level name (e.g., @home_user_dir)")
    parser.add_argument("-u", "--undo", action="store_true", help="Return a mounted subvolume to a regular directory")
    args = parser.parse_args()
    if args.undo and args.name is not None:
        parser.error("--name cannot be combined with --undo")
    ensure_root()

    def interrupted(signum: int, _frame: object) -> None:
        raise ConversionError(f"Interrupted by {signal.Signals(signum).name}")

    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT):
        signal.signal(sig, interrupted)
    try:
        if args.undo:
            revert_directory(args.path)
        else:
            convert_directory(args.path, custom_subvol_name=args.name)
    except KeyboardInterrupt:
        die("Interrupted", 130)
    except (ConversionError, OSError, UnicodeError) as exc:
        die(str(exc))


if __name__ == "__main__":
    main()
