#!/usr/bin/env python3
"""
==============================================================================
 UNIVERSAL DRIVE MANAGER — PLATINUM HYBRID EDITION (2026.09 / ARCH BLEEDING-EDGE)
 ------------------------------------------------------------------------------
 Target stack : Arch Linux, Linux 7.3+, Python 3.15+, util-linux 2.42+,
                cryptsetup 2.8+, systemd 262+, lsof 4.99+
 Runtime deps : python-keyring python-secretstorage python-rich
                util-linux cryptsetup lsof sudo systemd (systemd-run, systemctl)
 FTP deps     : rclone fuse3, user systemd manager and Secret Service

 Design:
  - Kernel-native truth sources: /proc/self/mountinfo (mount table, parent_id hierarchy,
    maj:min match, btrfs anon-dev aware), /sys/class/block/*/holders + dm/uuid (crypt mapping
    discovery, ghost-mapper detection), /sys/.../queue/rotational (TRIM policy).
    -> zero JSON schema drift, zero child pollution, fast sysfs checks.
  - sudo boundary: 'sudo -v' primed once on the main thread, kept alive by a
    daemon thread (sudo -n -v / 60 s), every privileged call is 'sudo -n ...'
    so a password prompt can never collide with a cryptsetup stdin pipe.
  - Interactive prompts (passphrase, busy-process resolver) run ONLY on the
    main thread. Workers return Outcome.NEEDS_PASSPHRASE and the main thread
    finishes them sequentially.
  - cryptsetup 2.8: open --type luks|bitlk --tries 1 --key-file - ;
    --allow-discards + --perf-no_{read,write}_workqueue only on non-rotational
    media; close -> 5x retry -> close --deferred (reported as failure until gone).
  - mount 2.42: mount -i --mkdir -t <fs> -o <opts> --source UUID=... --target ...;
    NTFS uses the 7.1+ in-kernel 'ntfs' driver (mount -i -t ntfs); 'ntfs3' is retired.
  - TRIM: fstrim dispatched as a transient systemd unit (systemd-run --collect
    --no-block, idle IO class); cancelled safely during teardown.
  - CPU Accelerator: On hybrid architectures (e.g. Intel Alder Lake i7-12700H),
    temporarily onlines performance cores during crypto KDF execution.
  - Lifecycle Hooks: Configurable pre_lock, post_lock, and post_unlock commands.
  - Strict exit codes: 0 ok, 1 operational failure, 130 user cancel.
==============================================================================
"""

import configparser
from contextlib import nullcontext
lazy import shutil

import argparse
import atexit
import enum
import fcntl
import getpass
import json
import os
import re
import signal
import stat
import subprocess
import sys
import threading
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NamedTuple

try:
    import keyring
    import keyring.errors
    import secretstorage
    from rich.align import Align
    from rich.console import Console
    from rich.markup import escape
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.table import Table
except ImportError as exc:
    sys.stderr.write(
        f"[ERROR] Missing Python library: {exc.name}\n"
        "        Install once: sudo pacman -S --needed python-keyring python-secretstorage python-rich\n"
    )
    sys.exit(1)

# ------------------------------------------------------------------------------
#  CONSTANTS
# ------------------------------------------------------------------------------
VERSION: Final = "2026.10.3"
FILESYSTEM_TIMEOUT: Final = 15          # by-uuid poll per UUID (seconds, single deadline)
CRYPTSETUP_TIMEOUT: Final = 180         # argon2id on a throttled laptop can take >30 s
LOCK_MAX_RETRIES: Final = 5
LOCK_RETRY_DELAY: Final = 1.0
UMOUNT_MAX_ATTEMPTS: Final = 5
SIGTERM_GRACE: Final = 5.0
KEYRING_SERVICE: Final = "drive_manager"
KEYRING_GET_TIMEOUT: Final = 10
KEYRING_SET_TIMEOUT: Final = 60
SUDO_KEEPALIVE_INTERVAL: Final = 60
MAX_PARALLEL: Final = 8
MAX_PARALLEL_KDF: Final = 2             # concurrent Argon2 opens (RAM pressure)
ATTEMPT_HISTORY_CAP: Final = 50
ATTEMPT_HISTORY_SHOW: Final = 6
HOOK_TIMEOUT: Final = 30.0

EXIT_OK: Final = 0
EXIT_FAIL: Final = 1
EXIT_CANCEL: Final = 130

NON_POSIX_FSTYPES: Final = frozenset({"ntfs", "vfat", "exfat", "msdos"})
TRIM_FSTYPES: Final = frozenset({"ext4", "xfs", "f2fs", "vfat", "exfat", "ntfs", "btrfs"})
FSTYPE_ALIASES: Final = {"fat32": "vfat", "fat": "vfat"}
PRUNE_ROOTS: Final = (Path("/mnt"), Path("/media"), Path("/run/media"), Path("/home"))
SYSFS_CPU: Final = Path("/sys/devices/system/cpu")

console = Console()
err_console = Console(stderr=True)
print_lock = threading.RLock()
KDF_SLOTS = threading.Semaphore(MAX_PARALLEL_KDF)
_lock_fd: int | None = None


def kernel_ntfs_driver() -> str:
    """The NTFS mount type: always 'ntfs' (the 7.1+ in-kernel implementation)."""
    try:
        fs_list = Path("/proc/filesystems").read_text().split()
        if "ntfs" not in fs_list:
            res = run(["modprobe", "-n", "ntfs"], new_session=False)
            if not res.ok:
                warn("Kernel module 'ntfs' not found; NTFS mounts may fail unless built into kernel.")
    except OSError:
        pass
    return "ntfs"


def is_ntfs_fstype(fstype: str | None) -> bool:
    return (fstype or "").lower() == "ntfs"


# ------------------------------------------------------------------------------
#  DATA STRUCTURES
# ------------------------------------------------------------------------------
class DriveType(enum.StrEnum):
    PROTECTED = "PROTECTED"
    SIMPLE = "SIMPLE"
    FTP = "FTP"


class Outcome(enum.Enum):
    OK = enum.auto()
    FAILED = enum.auto()
    NEEDS_PASSPHRASE = enum.auto()   # worker could not finish without an interactive prompt


@dataclass(slots=True, frozen=True)
class Drive:
    name: str
    type: DriveType
    mountpoint: Path
    outer_uuid: str | None
    inner_uuid: str | None
    hint: str | None
    fstype: str | None
    mount_options: tuple[str, ...]
    symlinks: tuple[Path, ...]
    post_unlock: tuple[str, ...] = ()
    pre_lock: tuple[str, ...] = ()
    post_lock: tuple[str, ...] = ()

    host: str | None = None
    port: int = 21
    remote: str | None = None

    @property
    def fs_uuid(self) -> str | None:
        """UUID of the mountable filesystem (inner for PROTECTED, outer for SIMPLE)."""
        return self.inner_uuid if self.type is DriveType.PROTECTED and self.inner_uuid else self.outer_uuid

    @property
    def mapper_name(self) -> str:
        """Deterministic dm name, identical to the systemd/udisks convention."""
        return f"luks-{self.outer_uuid}"

    @property
    def mapper_path(self) -> Path:
        return Path("/dev/mapper") / self.mapper_name


class MountEntry(NamedTuple):
    mount_id: int
    parent_id: int
    majmin: str
    root: str
    target: Path
    fstype: str
    source: str
    options: str
    super_options: str


class CryptMapping(NamedTuple):
    name: str
    dm_node: Path      # /dev/dm-N
    dm_uuid: str       # CRYPT-LUKS2-<hex>-<name> / CRYPT-BITLK-...


class Cmd(NamedTuple):
    rc: int
    out: str
    errtxt: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


# ------------------------------------------------------------------------------
#  LOGGING (THREAD-SAFE)
# ------------------------------------------------------------------------------
def log(msg: str) -> None:
    with print_lock:
        console.print(f"[bold blue]\\[DRIVE][/] {msg}")


def success(msg: str) -> None:
    with print_lock:
        console.print(f"[bold green]\\[SUCCESS][/] {msg}")


def warn(msg: str) -> None:
    with print_lock:
        console.print(f"[bold magenta]\\[WARN][/] {msg}")


def err(msg: str) -> None:
    with print_lock:
        err_console.print(f"[bold red]\\[ERROR][/] {msg}")


def hint_msg(msg: str) -> None:
    with print_lock:
        console.print(f"[bold yellow]\\[HINT][/] {msg}")


def cancel_exit() -> None:
    with print_lock:
        console.print()
        err("Cancelled by user.")
    sys.exit(EXIT_CANCEL)


# ------------------------------------------------------------------------------
#  SUBPROCESS PRIMITIVES
# ------------------------------------------------------------------------------
def _terminate_child(proc: subprocess.Popen[bytes], *, use_pg: bool) -> None:
    """Stop and reap a timed-out command, including same-process-group children."""
    if use_pg:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    else:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
    try:
        proc.communicate(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    if use_pg:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if proc.poll() is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        err(f"Timed-out child PID {proc.pid} is uninterruptible; inspect it before retrying.")


def run(argv: list[str], *, stdin: bytes | None = None, timeout: float | None = None,
        new_session: bool = True, env: dict[str, str] | None = None) -> Cmd:
    """Run argv (shell=False), always capturing output. Never raises.

    new_session=True isolates the child in its own process group so a timeout
    can reap the whole group. sudo callers MUST pass False: the sudo ticket
    is bound to our terminal session, and a setsid child never matches it.
    """
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=new_session,
            env=env,
        )
    except OSError as e:
        return Cmd(-1, "", f"{argv[0]}: {e.strerror}")
    try:
        out, errtxt = proc.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_child(proc, use_pg=new_session)
        return Cmd(-1, "", f"timed out after {timeout}s: {' '.join(argv)}")
    except BaseException:
        _terminate_child(proc, use_pg=new_session)
        raise
    return Cmd(proc.returncode, out.decode(errors="replace"), errtxt.decode(errors="replace"))


class Sudo:
    """sudo timestamp lifecycle. prime() is the ONLY interactive sudo call."""
    _stop = threading.Event()
    _started = False

    @staticmethod
    def prime() -> None:
        with print_lock:
            rc = subprocess.run(["sudo", "-v"]).returncode
        if rc != 0:
            err("sudo authentication failed. Cannot proceed.")
            sys.exit(EXIT_FAIL)

    @classmethod
    def ensure(cls) -> None:
        if run(["sudo", "-n", "-v"], new_session=False).ok:
            return
        log("sudo timestamp expired; re-authenticating...")
        cls.prime()

    @classmethod
    def start_keepalive(cls) -> None:
        if cls._started:
            return
        cls._started = True

        def loop() -> None:
            while not cls._stop.wait(SUDO_KEEPALIVE_INTERVAL):
                run(["sudo", "-n", "-v"], new_session=False)

        threading.Thread(target=loop, daemon=True, name="sudo-keepalive").start()


def sudo(argv: list[str], *, stdin: bytes | None = None, timeout: float | None = None, report: bool = True) -> Cmd:
    """Privileged call. 'sudo -n' guarantees no password prompt can ever touch stdin."""
    res = run(["sudo", "-n", *argv], stdin=stdin, timeout=timeout, new_session=False)
    if res.rc == 1 and "password is required" in res.errtxt:
        if threading.current_thread() is threading.main_thread():
            Sudo.ensure()
            res = run(["sudo", "-n", *argv], stdin=stdin, timeout=timeout, new_session=False)
        else:
            err("sudo timestamp expired during worker execution.")
    if not res.ok and report and res.errtxt.strip():
        err(f"{escape(argv[0])} failed (rc={res.rc}): {escape(res.errtxt.strip())}")
    return res


# ------------------------------------------------------------------------------
#  SECURITY & ISOLATION
# ------------------------------------------------------------------------------
def prevent_root_execution() -> None:
    if os.geteuid() == 0:
        err("Do NOT run this script with sudo.")
        console.print("Running as root breaks access to your user's Secret Service keyring.")
        console.print("Privileges are requested internally, per command, via 'sudo -n'.")
        sys.exit(EXIT_FAIL)


def check_dependencies() -> None:
    deps = ["sudo", "mount", "umount", "lsblk", "cryptsetup", "lsof", "blockdev", "fstrim", "systemd-run", "systemctl"]
    missing = [d for d in deps if shutil.which(d) is None]
    if missing:
        err(f"Missing required commands: {', '.join(missing)}")
        hint_msg("sudo pacman -S --needed util-linux cryptsetup lsof systemd")
        sys.exit(EXIT_FAIL)


def get_runtime_dir() -> Path:
    """$XDG_RUNTIME_DIR/drive_manager (tmpfs, per-user, wiped at logout). Verified via O_NOFOLLOW|O_DIRECTORY fstat."""
    env = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if not env:
        err("XDG_RUNTIME_DIR is not set. Run from a systemd-logind session (TTY, SSH or desktop).")
        sys.exit(EXIT_FAIL)
    path = Path(env) / "drive_manager"
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as e:
        err(f"Security hazard: cannot open {path} safely ({e.strerror}). Symlink hijack?")
        sys.exit(EXIT_FAIL)
    try:
        st = os.fstat(fd)
    finally:
        os.close(fd)
    if st.st_uid != os.getuid() or (st.st_mode & 0o077):
        err(f"Security hazard: {path} is not owned by uid {os.getuid()} with mode 0700.")
        sys.exit(EXIT_FAIL)
    return path


def acquire_lock() -> None:
    """Exclusive kernel flock in the runtime dir; PID stamped for diagnostics."""
    global _lock_fd
    lock_path = get_runtime_dir() / "drive_manager.lock"
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    except OSError as e:
        err(f"Could not open lock file {lock_path}: {e.strerror}")
        sys.exit(EXIT_FAIL)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        holder = ""
        try:
            holder = os.read(fd, 32).decode(errors="replace").strip()
        except OSError:
            pass
        os.close(fd)
        err(f"Another drive_manager instance is running{f' (PID {holder})' if holder else ''}.")
        sys.exit(EXIT_FAIL)
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode())
    _lock_fd = fd  # intentionally never closed: released by the kernel at exit


# ------------------------------------------------------------------------------
#  KERNEL TRUTH SOURCES: /proc/self/mountinfo, /sys/class/block, /dev/disk/by-uuid
# ------------------------------------------------------------------------------
_OCTAL_ESC = re.compile(r"\\([0-7]{3})")


def _unescape(s: str) -> str:
    return _OCTAL_ESC.sub(lambda m: chr(int(m.group(1), 8)), s)


def read_mountinfo(entries: list[str] | None = None) -> list[MountEntry]:
    if entries is None:
        entries = Path("/proc/self/mountinfo").read_text().splitlines()
    out: list[MountEntry] = []
    for line in entries:
        pre, sep, post = line.partition(" - ")
        if not sep:
            continue
        pf = pre.split(" ")
        po = post.split(" ")
        if len(pf) < 6 or len(po) < 3:
            continue
        try:
            out.append(MountEntry(int(pf[0]), int(pf[1]), pf[2], _unescape(pf[3]),
                                  Path(_unescape(pf[4])), po[0], _unescape(po[1]), pf[5], po[2]))
        except (ValueError, IndexError):
            continue
    return out


def device_majmin(dev: Path | None) -> str | None:
    if dev is None:
        return None
    try:
        st = os.stat(dev)
    except OSError:
        return None
    if not stat.S_ISBLK(st.st_mode):
        return None
    return f"{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}"


def mounts_for_device(dev: Path | None, snapshot: list[MountEntry] | None = None) -> list[Path]:
    """Every mountpoint backed by dev. Matches maj:min, and source path for btrfs (anonymous superblock dev)."""
    mm = device_majmin(dev)
    if not mm:
        return []
    if snapshot is None:
        snapshot = read_mountinfo()
    hits: set[Path] = set()
    for e in snapshot:
        if e.majmin == mm or (e.source.startswith("/dev/") and device_majmin(Path(e.source)) == mm):
            hits.add(e.target)
    return sorted(hits)


def mount_entry_for(target: Path, snapshot: list[MountEntry] | None = None) -> MountEntry | None:
    """Topmost mount at target, resolved by mountinfo parent IDs (not line order)."""
    # Configured paths are canonical already; resolving a live FUSE target can hang.
    t = target.absolute()
    if snapshot is None:
        snapshot = read_mountinfo()
    stack = [e for e in snapshot if e.target == t]
    if not stack:
        return None
    parents = {e.parent_id for e in stack}
    top = [e for e in stack if e.mount_id not in parents]
    if len(top) == 1:
        return top[0]
    # Degenerate stack: fall back to the last line rather than refusing outright.
    return stack[-1]


def entry_matches(entry: MountEntry, dev: Path | None, fstype: str | None) -> bool:
    """True when entry is backed by dev with the expected fstype.

    A FUSE mount (e.g. ntfs-3g 'fuseblk') is never a managed kernel mount.
    """
    mm = device_majmin(dev)
    if not mm:
        return False
    if entry.majmin != mm and not (
        entry.source.startswith("/dev/") and device_majmin(Path(entry.source)) == mm
    ):
        return False
    if entry.fstype.startswith("fuse"):
        return False
    if fstype is None:
        return True
    return entry.fstype.lower() == fstype.lower()


def resolve_device(uuid: str | None) -> Path | None:
    if not uuid:
        return None
    p = Path("/dev/disk/by-uuid") / uuid
    try:
        return p.resolve(strict=True)
    except OSError:
        return None


def sysfs_block(dev: Path) -> Path | None:
    node = Path("/sys/class/block") / dev.resolve().name
    return node if node.exists() else None


def is_rotational(dev: Path | None) -> bool:
    """queue/rotational of the device, its parent disk, or (dm) any of its slaves."""
    if dev is None:
        return False
    node = sysfs_block(dev)
    if node is None:
        return False
    real = node.resolve()
    for cand in (real / "queue" / "rotational", real.parent / "queue" / "rotational"):
        if cand.is_file():
            return cand.read_text().strip() == "1"
    slaves = real / "slaves"
    if slaves.is_dir():
        return any(is_rotational(Path("/dev") / s.name) for s in slaves.iterdir())
    return False


def probe_fstype(dev: Path | None) -> str | None:
    """libblkid superblock type: udev database first (no subprocess), lsblk fallback."""
    if dev is None:
        return None
    mm = device_majmin(dev)
    if mm is not None:
        try:
            with open(f"/run/udev/data/b{mm}", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.startswith("E:ID_FS_TYPE="):
                        return normalize_fstype(line[13:].strip())
        except OSError:
            pass
    res = run(["lsblk", "--noheadings", "--nodeps", "-o", "FSTYPE", str(dev)])
    if not res.ok:
        return None
    return normalize_fstype(res.out.strip() or None)


def _crypt_mapping_from_dm(dm_dir: Path) -> CryptMapping | None:
    name_f, uuid_f = dm_dir / "dm" / "name", dm_dir / "dm" / "uuid"
    if not name_f.is_file():
        return None
    uuid = uuid_f.read_text().strip() if uuid_f.is_file() else ""
    if not uuid.startswith("CRYPT-"):
        return None
    return CryptMapping(name_f.read_text().strip(), Path("/dev") / dm_dir.resolve().name, uuid)


def find_crypt_mapping(drive: Drive, outer_dev: Path | None) -> CryptMapping | None:
    """Live holder of the outer partition; falls back to a dm-uuid scan (covers ghost mappers after unplug)."""
    if outer_dev is not None:
        node = sysfs_block(outer_dev)
        holders = node / "holders" if node else None
        if holders and holders.is_dir():
            for h in sorted(holders.iterdir()):
                m = _crypt_mapping_from_dm(h)
                if m:
                    return m
    hexuuid = drive.outer_uuid.replace("-", "").lower()
    for dm_dir in sorted(Path("/sys/class/block").glob("dm-*")):
        m = _crypt_mapping_from_dm(dm_dir)
        if not m or not (m.name == drive.mapper_name or hexuuid in m.dm_uuid.lower()):
            continue
        # A same-named mapper backed by a different device is not ours (ghost/clone).
        if outer_dev is not None:
            try:
                if outer_dev.resolve().name not in {s.name for s in (dm_dir / "slaves").iterdir()}:
                    continue
            except OSError:
                continue
        return m
    return None


def mapping_is_live(mapping: CryptMapping, outer_dev: Path | None = None) -> bool:
    """suspended==0 with slaves present (and the expected backing device when known).

    Pure sysfs: no speculative O_DIRECT reads (no disk I/O, no false negatives
    on slow USB, no extra privileged spawns per check).
    """
    node = Path("/sys/class/block") / mapping.dm_node.name
    if not node.exists():
        return False
    susp = node / "dm" / "suspended"
    try:
        if susp.is_file() and susp.read_text().strip() != "0":
            return False
    except OSError:
        return False
    try:
        slaves = [s.name for s in (node / "slaves").iterdir()]
    except OSError:
        return False
    if not slaves:
        return False
    if outer_dev is not None:
        try:
            return outer_dev.resolve().name in slaves
        except OSError:
            return False
    return True


def wait_for_device(uuid: str, timeout: int = FILESYSTEM_TIMEOUT) -> Path | None:
    """Poll only this UUID to a single deadline (no global udevadm settle)."""
    deadline = time.monotonic() + timeout
    while True:
        dev = resolve_device(uuid)
        if dev or time.monotonic() >= deadline:
            return dev
        time.sleep(0.25)


# ------------------------------------------------------------------------------
#  FAILED-ATTEMPT HISTORY (tmpfs, 0600, atomic, wiped at logout)
# ------------------------------------------------------------------------------
def _attempts_path(name: str) -> Path:
    return get_runtime_dir() / f"attempts_{name}.json"


def load_attempts(name: str) -> list[str]:
    path = _attempts_path(name)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return []
    except OSError:
        return []
    try:
        st = os.fstat(fd)
        if st.st_uid != os.getuid() or (st.st_mode & 0o077) or not stat.S_ISREG(st.st_mode):
            os.close(fd)
            path.unlink(missing_ok=True)
            return []
        with os.fdopen(fd, "r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    return [x for x in data if isinstance(x, str)] if isinstance(data, list) else []


def save_attempts(name: str, attempts: list[str]) -> None:
    path = _attempts_path(name)
    tmp = path.with_suffix(".tmp")
    try:
        fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(attempts[-ATTEMPT_HISTORY_CAP:], f)
        os.replace(tmp, path)
    except OSError as e:
        warn(f"Could not persist attempt history: {e.strerror}")


def record_failed_attempt(name: str, secret: str) -> None:
    attempts = load_attempts(name)
    if secret not in attempts:
        attempts.append(secret)
        save_attempts(name, attempts)


def clear_attempts(name: str) -> None:
    _attempts_path(name).unlink(missing_ok=True)


# ------------------------------------------------------------------------------
#  KEYRING (SECRET SERVICE) — every D-Bus wait is bounded
# ------------------------------------------------------------------------------
def is_gui_available() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def _secret_service_collection():
    """Default collection when keyring resolves to a Secret Service backend; None for other backends."""
    module = type(keyring.get_keyring()).__module__.lower()
    if not any(tag in module for tag in ("secretservice", "libsecret", "chainer")):
        return None
    conn = secretstorage.dbus_init()
    try:
        return secretstorage.get_default_collection(conn)
    except BaseException:
        conn.close()
        raise


def _call_with_timeout(fn, timeout: float):
    box: dict[str, object] = {}

    def worker() -> None:
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 - re-raised on the caller thread
            box["error"] = e

    t = threading.Thread(target=worker, daemon=True, name="keyring-io")
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"Secret Service did not answer within {timeout}s")
    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box.get("value")


def ensure_keyring_unlocked() -> bool:
    def probe() -> bool:
        # All operations on this connection stay in one bounded worker. Close it
        # on success/failure; repeated credential lookups must not leak D-Bus FDs.
        coll = _secret_service_collection()
        if coll is None:
            return True
        try:
            if not coll.is_locked():
                return True
            if not is_gui_available():
                log("Keyring is locked and no WAYLAND_DISPLAY is set. Using terminal prompt.")
                return False
            log("Keyring is locked. Requesting unlock through the desktop Secret Service prompt...")
            dismissed = coll.unlock()
            if dismissed or coll.is_locked():
                log("Keyring unlock was dismissed.")
                return False
            success("Keyring unlocked.")
            return True
        finally:
            coll.connection.close()

    try:
        timeout = KEYRING_SET_TIMEOUT if is_gui_available() else KEYRING_GET_TIMEOUT
        return bool(_call_with_timeout(probe, timeout))
    except Exception as exc:
        log(f"Secret Service unavailable ({escape(type(exc).__name__)}); using terminal prompt.")
        return False


def keyring_get(name: str, timeout: float = KEYRING_GET_TIMEOUT) -> str | None:
    if not ensure_keyring_unlocked():
        return None
    try:
        value = _call_with_timeout(lambda: keyring.get_password(KEYRING_SERVICE, name), timeout)
    except TimeoutError:
        log("Keyring lookup timed out. Falling through to terminal prompt.")
        return None
    except Exception as e:
        log(f"Keyring lookup error ({escape(type(e).__name__)}). Falling through to terminal prompt.")
        return None
    return value if isinstance(value, str) and value else None


def keyring_set(name: str, secret: str, timeout: float = KEYRING_SET_TIMEOUT) -> bool:
    if not ensure_keyring_unlocked():
        err("Keyring locked or unavailable; passphrase NOT saved.")
        return False
    try:
        _call_with_timeout(lambda: keyring.set_password(KEYRING_SERVICE, name, secret), timeout)
    except TimeoutError:
        err(f"Keyring store timed out after {timeout}s; passphrase NOT saved.")
        return False
    except keyring.errors.KeyringLocked:
        err("Keyring is locked; passphrase NOT saved.")
        return False
    except Exception as e:
        err(f"Keyring store error: {escape(str(e))}")
        return False
    return True


# ------------------------------------------------------------------------------
#  PASSPHRASE PROMPT (WITH PREVIOUS ATTEMPTS PREVIEW)
# ------------------------------------------------------------------------------
def prompt_passphrase(drive: Drive) -> str:
    """MAIN THREAD ONLY. Shows hint + history panel, returns non-empty passphrase. Raises KeyboardInterrupt/EOFError."""
    while True:
        with print_lock:
            if drive.hint:
                hint_msg(f"Hint: {escape(drive.hint)}")
            attempts = load_attempts(drive.name)
            if attempts:
                shown = attempts[-ATTEMPT_HISTORY_SHOW:]
                hidden = len(attempts) - len(shown)
                lines: list[str] = []
                if hidden:
                    lines.append(f"[dim]... {hidden} older attempt{'s' if hidden > 1 else ''} hidden ...[/]")
                lines.extend(f"[red]✗[/] {escape(a)}" for a in shown)
                console.print(Align.right(Panel("\n".join(lines), title="[yellow]Previously Tried[/]",
                                                border_style="yellow", expand=False)))
            secret = Prompt.ask(
                f"Enter passphrase for [bold]{escape(drive.name)}[/] (/dev/disk/by-uuid/[bold cyan]{drive.outer_uuid}[/])",
                password=True,
            )
        secret = secret.rstrip("\r\n")
        if secret:
            return secret


# ------------------------------------------------------------------------------
#  CPU ACCELERATOR (hybrid topologies with user-offlined P-cores)
# ------------------------------------------------------------------------------
def parse_cpulist(text: str) -> list[int]:
    cpus: list[int] = []
    for part in text.strip().split(","):
        if not part:
            continue
        lo, _, hi = part.partition("-")
        cpus.extend(range(int(lo), int(hi or lo) + 1))
    return cpus


class CPUAccelerator:
    """Temporarily onlines offline performance cores; restores on exit, exception, SIGTERM/SIGHUP (not SIGKILL)."""

    def __init__(self) -> None:
        self.enabled: list[int] = []
        atexit.register(self.restore)

    @staticmethod
    def performance_cores() -> list[int]:
        override = os.environ.get("DRIVE_MANAGER_PCORES", "").strip()
        if override:
            return parse_cpulist(override)
        intel = Path("/sys/devices/cpu_core/cpus")          # Intel hybrid PMU cpumask
        if intel.is_file():
            return parse_cpulist(intel.read_text())
        perf: dict[int, int] = {}                             # AMD/other: ACPI CPPC highest_perf spread
        for node in SYSFS_CPU.glob("cpu[0-9]*"):
            f = node / "acpi_cppc" / "highest_perf"
            if f.is_file():
                txt = f.read_text().strip()
                if txt.isdigit():
                    perf[int(node.name[3:])] = int(txt)
        if len(set(perf.values())) < 2:
            return []
        lo, hi = min(perf.values()), max(perf.values())
        if (hi - lo) / hi <= 0.15:
            return []
        mid = (lo + hi) / 2
        return sorted(c for c, v in perf.items() if v >= mid)

    def __enter__(self) -> "CPUAccelerator":
        offline = []
        for cpu in self.performance_cores():
            f = SYSFS_CPU / f"cpu{cpu}" / "online"
            try:
                if f.is_file() and f.read_text().strip() == "0":
                    offline.append(cpu)
            except OSError:
                pass
        if offline:
            log(f"Offline performance cores {offline}: enabling for the duration of this run...")
            for cpu in offline:
                if sudo(["tee", str(SYSFS_CPU / f"cpu{cpu}" / "online")], stdin=b"1", report=False).ok:
                    self.enabled.append(cpu)
                else:
                    warn(f"Could not online cpu{cpu} (CONFIG_HOTPLUG_CPU disabled or cpu locked).")
        return self

    def __exit__(self, *exc) -> None:
        self.restore()

    def restore(self) -> None:
        if not self.enabled:
            return
        log("Restoring CPU power-saving state (offlining performance cores)...")
        for cpu in list(self.enabled):
            path = SYSFS_CPU / f"cpu{cpu}" / "online"
            for _ in range(5):
                if sudo(["tee", str(path)], stdin=b"0", report=False).ok:
                    break
                time.sleep(0.05)
        self.enabled.clear()


# ------------------------------------------------------------------------------
#  LIFECYCLE HOOKS (post_unlock, pre_lock, post_lock)
# ------------------------------------------------------------------------------
def run_hooks(drive: Drive, stage: str, commands: tuple[str, ...]) -> bool:
    """Execute configured lifecycle commands unprivileged as the invoking user."""
    if not commands:
        return True
    all_ok = True
    log(f"Running {stage} hooks for '{drive.name}' ({len(commands)} command{'s' if len(commands) > 1 else ''})...")
    env = os.environ.copy()
    env["DRIVE_NAME"] = drive.name
    env["DRIVE_MOUNTPOINT"] = str(drive.mountpoint)
    env["DRIVE_TYPE"] = drive.type.value
    env["DRIVE_FSTYPE"] = drive.fstype or ""
    for cmd_str in commands:
        log(f"\\[{escape(drive.name)}] {stage}: {escape(cmd_str)}")
        try:
            res = run(["/bin/sh", "-c", cmd_str], timeout=HOOK_TIMEOUT, new_session=True, env=env)
            if res.ok:
                if res.out.strip():
                    for line in res.out.strip().splitlines():
                        console.print(f"  [dim]│[/] {escape(line)}")
            else:
                all_ok = False
                err_text = res.errtxt.strip() or res.out.strip() or f"rc={res.rc}"
                err(f"\\[{escape(drive.name)}] {stage} hook failed: {escape(err_text)}")
        except Exception as e:
            all_ok = False
            err(f"\\[{escape(drive.name)}] {stage} hook execution error: {escape(repr(e))}")
    return all_ok


# ------------------------------------------------------------------------------
#  CRYPT CONTAINER OPERATIONS
# ------------------------------------------------------------------------------
def container_type(outer_dev: Path) -> tuple[str | None, str | None]:
    probe = probe_fstype(outer_dev)
    match (probe or "").lower():
        case "crypto_luks":
            return "luks", probe
        case "bitlocker":
            return "bitlk", probe
        case _:
            return None, probe


def open_container(drive: Drive, outer_dev: Path, secret: str) -> bool:
    ctype, probe = container_type(outer_dev)
    if ctype is None:
        err(f"'{drive.name}': no LUKS/BitLocker superblock on {outer_dev} (blkid TYPE={probe or 'none'}).")
        hint_msg(f"Verify with: lsblk -d -o NAME,FSTYPE,UUID {outer_dev}")
        return False
    argv = ["cryptsetup", "open", "--type", ctype, "--tries", "1", "--key-file", "-"]
    if not is_rotational(outer_dev):
        argv.append("--allow-discards")
        if ctype == "luks":
            argv += ["--perf-no_read_workqueue", "--perf-no_write_workqueue"]
    argv += [str(outer_dev), drive.mapper_name]
    with KDF_SLOTS:  # cap concurrent Argon2 memory demand; filesystem work stays parallel
        res = sudo(argv, stdin=secret.encode(), timeout=CRYPTSETUP_TIMEOUT, report=False)
    if res.ok:
        return True
    if res.rc == -1:
        err(f"cryptsetup timed out for '{drive.name}': {escape(res.errtxt)}")
    elif res.rc == 2:
        err(f"Decryption failed for '{drive.name}': passphrase rejected.")
    else:
        err(f"cryptsetup open failed for '{drive.name}' (rc={res.rc}): {escape(res.errtxt.strip())}")
    return False


def close_container(name: str, *, deferred: bool = False, report: bool = False) -> bool:
    argv = ["cryptsetup", "close"] + (["--deferred"] if deferred else []) + [name]
    return sudo(argv, report=report).ok


def crypt_forensics(mapping: CryptMapping) -> None:
    log(f"Forensics for /dev/mapper/{mapping.name} ({mapping.dm_node})...")
    res = sudo(["lsof", "-w", str(mapping.dm_node), f"/dev/mapper/{mapping.name}"], report=False)
    if res.out.strip():
        with print_lock:
            console.print(Panel(escape(res.out.strip()), title="Processes holding the crypt node", border_style="red"))
    holders = Path("/sys/class/block") / mapping.dm_node.name / "holders"
    kernel_holders = sorted(h.name for h in holders.iterdir()) if holders.is_dir() else []
    if kernel_holders:
        hint_msg(f"Kernel block holders (LVM/RAID/another dm layer): {', '.join(kernel_holders)}")
    status = sudo(["cryptsetup", "status", mapping.name], report=False)
    if status.out.strip():
        with print_lock:
            console.print(Panel(escape(status.out.strip()), title="cryptsetup status", border_style="yellow"))
    hint_msg("Next: 'sudo dmesg | tail -n 30' ; 'findmnt --json -v' ; "
             f"'sudo cryptsetup close --deferred {mapping.name}' once the holder is gone.")


# ------------------------------------------------------------------------------
#  UNMOUNT / BUSY-PROCESS RESOLVER (MAIN THREAD FOR PROMPTS)
# ------------------------------------------------------------------------------
def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def parse_lsof(out: str) -> list[dict[str, str]]:
    procs: list[dict[str, str]] = []
    cur: dict[str, str] | None = None
    for line in out.splitlines():
        if not line:
            continue
        tag, val = line[0], line[1:]
        if tag == "p":
            cur = {"pid": val, "cmd": "unknown", "user": "unknown"}
            procs.append(cur)
        elif cur is not None and tag == "c":
            cur["cmd"] = val
        elif cur is not None and tag == "u":
            cur["user"] = val
    seen: set[str] = set()
    unique = []
    for p in procs:
        if p["pid"] not in seen and p["pid"].isdigit():
            seen.add(p["pid"])
            unique.append(p)
    return unique


def resolve_busy_processes(mountpoint: Path, *, interactive: bool) -> bool:
    """lsof via sudo (defeats hidepid=2). Returns True when at least one holder was terminated."""
    res = sudo(["lsof", "-w", "-F", "pcu", "+f", "--", str(mountpoint)], report=False)
    procs = parse_lsof(res.out)
    if not procs:
        return False
    with print_lock:
        console.print(Panel(
            "[bold red]FILESYSTEM IS BUSY[/]\n\n"
            f"The following processes hold files under [bold white]{escape(str(mountpoint))}[/].",
            title="Filesystem Locked", border_style="red"))
        table = Table(show_header=True, header_style="bold yellow", border_style="yellow")
        table.add_column("COMMAND", style="cyan")
        table.add_column("PID", justify="right", style="yellow")
        table.add_column("USER")
        for p in procs:
            table.add_row(escape(p["cmd"]), p["pid"], escape(p["user"]))
        console.print(table)
    if not interactive:
        hint_msg("Parallel mode: busy-process resolution is deferred to the main thread.")
        return False
    acted = False
    for p in procs:
        pid = int(p["pid"])
        if not process_alive(pid):
            continue
        with print_lock:
            ans = Prompt.ask(f"Terminate [bold cyan]{escape(p['cmd'])}[/] (PID [bold yellow]{pid}[/])?",
                             choices=["y", "n"], default="y")
        if ans != "y":
            continue
        if not sudo(["kill", "-TERM", str(pid)], report=True).ok:
            continue
        deadline = time.monotonic() + SIGTERM_GRACE
        while process_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.25)
        if process_alive(pid):
            warn(f"PID {pid} ignored SIGTERM; sending SIGKILL.")
            sudo(["kill", "-KILL", str(pid)], report=True)
            time.sleep(0.25)
        if not process_alive(pid):
            success(f"Terminated {escape(p['cmd'])} (PID {pid}).")
            acted = True
    return acted


def unmount_path(mountpoint: Path, dev: Path | None = None, fstype: str | None = None,
                 *, interactive: bool) -> bool:
    if mount_entry_for(mountpoint) is None:
        return True
    # Never unmount a foreign filesystem occupying our target: a different
    # device or an unsupported type (e.g. FUSE) is a conflict, not our mount.
    if dev is not None or fstype is not None:
        occupant = mount_entry_for(mountpoint)
        if occupant is not None and not entry_matches(occupant, dev, fstype):
            err(f"Refusing to unmount unrelated {occupant.fstype} filesystem at {escape(str(mountpoint))}.")
            return False
    log(f"Unmounting {escape(str(mountpoint))}...")
    for attempt in range(1, UMOUNT_MAX_ATTEMPTS + 1):
        res = sudo(["umount", "-i", str(mountpoint)], report=False)
        if res.ok or mount_entry_for(mountpoint) is None:
            log(f"Unmounted {escape(str(mountpoint))}.")
            return True
        busy = "busy" in res.errtxt.lower()
        if busy:
            log(f"{escape(str(mountpoint))} is busy (attempt {attempt}/{UMOUNT_MAX_ATTEMPTS}); scanning holders...")
            if not resolve_busy_processes(mountpoint, interactive=interactive):
                if not interactive:
                    break
                log("No userspace holder terminated; waiting for the kernel to settle...")
        else:
            err(f"umount {escape(str(mountpoint))}: {escape(res.errtxt.strip())}")
            break
        time.sleep(1)
    err(f"Failed to unmount {escape(str(mountpoint))}.")
    hint_msg(f"Inspect with: sudo lsof +f -- {mountpoint} ; findmnt --json -v --mountpoint {mountpoint}")
    return False


def prune_stale_dir(path: Path, keep: set[Path]) -> None:
    """rmdir the (now empty) stale mountpoint, then climb while parents are empty and inside a prune root.
    Never touches: $HOME itself, anything above the prune roots, configured mountpoints, non-empty or mounted dirs."""
    home = Path.home()
    p = path.resolve()
    first = True
    while True:
        if p in keep or p == home or p == Path("/") or p in PRUNE_ROOTS:
            return
        if not any(p.is_relative_to(r) for r in PRUNE_ROOTS):
            return
        if not first and p.is_relative_to(home):
            return  # never climb inside the active user's home
        try:
            if p.is_symlink() or not p.is_dir() or mount_entry_for(p) or any(p.iterdir()):
                return
            try:
                p.rmdir()  # unprivileged first: mountpoints we created are ours
            except PermissionError:
                if not sudo(["rmdir", str(p)], report=False).ok:
                    return
            except OSError:
                return
        except OSError:
            return
        log(f"Pruned empty stale directory {escape(str(p))}.")
        first = False
        p = p.parent


# ------------------------------------------------------------------------------
#  INTEGRATIONS: ownership + declarative symlinks (rename-not-delete)
# ------------------------------------------------------------------------------
def reconcile_integrations(drive: Drive) -> bool:
    """Heal ownership + symlinks. Atomic symlink swap; True when healthy."""
    uid, gid = os.getuid(), os.getgid()
    home = Path.home()
    target = drive.mountpoint.resolve()
    healthy = True
    if target.is_relative_to(home):
        try:
            st = target.stat()
            if st.st_uid != uid or st.st_gid != gid:
                log(f"Adjusting ownership of {escape(str(target))} to {uid}:{gid}...")
                if not sudo(["chown", f"{uid}:{gid}", str(target)]).ok:
                    healthy = False
        except OSError as e:
            warn(f"Could not stat {escape(str(target))}: {e.strerror}")
            healthy = False
    for link in drive.symlinks:
        tmp = link.with_name(f".{link.name}.drive-manager-{os.getpid()}")
        try:
            if link.is_symlink():
                current = (link.parent / os.readlink(link)).resolve()
                if current == target:
                    continue
                log(f"Re-pointing symlink {escape(str(link))} -> {escape(str(target))}")
            elif link.exists():
                backup = link.with_name(f"{link.name}.pre-drive-manager.{time.strftime('%Y%m%d-%H%M%S')}")
                warn(f"{escape(str(link))} is a real path; moving it to {escape(str(backup))} (nothing is deleted).")
                link.rename(backup)
            else:
                link.parent.mkdir(parents=True, exist_ok=True)
            tmp.symlink_to(target)
            os.replace(tmp, link)  # atomic: no window with a dangling link
            os.lchown(link, uid, gid)
            success(f"Symlink ready: {escape(str(link))} -> {escape(str(target))}")
        except OSError as e:
            healthy = False
            err(f"Symlink reconcile failed for {escape(str(link))}: {e.strerror}")
            hint_msg(f"Inspect with: ls -ld {link} ; findmnt --mountpoint {link}")
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    return healthy


# ------------------------------------------------------------------------------
#  MOUNT POLICY
# ------------------------------------------------------------------------------
def normalize_fstype(fs: str | None) -> str | None:
    if not fs:
        return None
    fs = fs.strip().lower()
    return FSTYPE_ALIASES.get(fs, fs)


def audit_mount_options(drive: Drive, fstype: str | None, rotational: bool) -> None:
    opts = set(drive.mount_options)
    if "force" in opts:
        warn(f"'{drive.name}': 'force' is set — this clears the Windows dirty bit and risks NTFS corruption.")
    if "prealloc" in opts and is_ntfs_fstype(fstype):
        warn(f"'{drive.name}': 'prealloc' is not an ntfs option (use 'preallocated_size='); mount would fail.")
    if "discard" in opts and rotational:
        warn(f"'{drive.name}': 'discard' on a rotational disk is a no-op; remove it.")
    if "discard" in opts and fstype == "ext4":
        hint_msg(f"'{drive.name}': ext4 synchronous 'discard' stalls unlink(); prefer the background fstrim this tool dispatches.")
    if "autodefrag" in opts and not rotational:
        warn(f"'{drive.name}': 'autodefrag' on an SSD multiplies write amplification; remove it.")
    if fstype == "btrfs" and not rotational and not any(o.startswith("discard") for o in opts):
        hint_msg(f"'{drive.name}': btrfs on SSD — add 'discard=async' for native non-blocking TRIM.")
    if "flush" in opts:
        warn(f"'{drive.name}': 'flush' destroys flash endurance; remove it.")


def build_mount_argv(drive: Drive, source: str, fstype: str | None) -> list[str]:
    uid, gid = os.getuid(), os.getgid()
    if is_ntfs_fstype(fstype):
        fstype = kernel_ntfs_driver()
    argv = ["mount", "-i", "--mkdir"]
    if fstype:
        argv += ["-t", fstype]
    options: list[str] = []
    if drive.mount_options:
        for opt in drive.mount_options:
            if opt.startswith("uid="):
                options.append(f"uid={uid}")
            elif opt.startswith("gid="):
                options.append(f"gid={gid}")
            else:
                options.append(opt)
    elif fstype in NON_POSIX_FSTYPES:
        options.append(f"uid={uid},gid={gid},dmask=022,fmask=133")
        log(f"Auto-configured ownership for non-POSIX filesystem {fstype.upper()}.")
    if options:
        argv += ["-o", ",".join(options)]
    argv += ["--source", source, "--target", str(drive.mountpoint)]
    return argv


def trim_unit(drive: Drive) -> str:
    """Stable unit name per drive so lock can cancel a queued TRIM before unmount."""
    return f"drive-manager-trim-{re.sub(r'[^a-zA-Z0-9_.-]', '_', drive.name)}.service"


def stop_trim(drive: Drive) -> bool:
    """Cancel a queued TRIM unit; a TRIM running after unmount could hit another fs.

    Best-effort only: never blocks lock. A missing unit ('not-found') is success.
    """
    unit = trim_unit(drive)
    try:
        res = sudo(["systemctl", "stop", unit], report=False)
        if res.ok:
            return True
        state = sudo(["systemctl", "show", "-p", "LoadState", "--value", unit], report=False)
        if state.ok and state.out.strip() == "not-found":
            return True  # --collect already unloaded the finished unit
    except SystemExit:
        pass
    return True


def dispatch_trim(drive: Drive, dev: Path | None, fstype: str | None, rotational: bool,
                  entry: MountEntry | None = None) -> None:
    if rotational or fstype not in TRIM_FSTYPES:
        return
    active = [o for o in drive.mount_options if o.startswith("discard")]
    if entry is not None:
        active += [o for o in (*entry.options.split(","), *entry.super_options.split(","))
                   if o == "discard" or o.startswith("discard=")]
    if active:
        return  # discard / discard=async already handles it at the FS layer
    unit = trim_unit(drive)
    res = sudo(["systemd-run", "--quiet", "--collect", "--no-block", f"--unit={unit}",
                "-p", "Nice=19", "-p", "IOSchedulingClass=idle",
                "fstrim", "--quiet-unsupported", str(drive.mountpoint)], report=False)
    if res.ok:
        log(f"Background TRIM dispatched for '{drive.name}' (journalctl -u {unit}).")
    else:
        warn(f"Could not dispatch TRIM for '{drive.name}': {escape(res.errtxt.strip())}")


# ------------------------------------------------------------------------------
#  FTP: user-service lifecycle, runtime credentials, persistent VFS write cache
# ------------------------------------------------------------------------------
def ftp_account(drive: Drive) -> str:
    return f"ftp:{drive.name}"


def ftp_credentials(secret: str) -> dict[str, str]:
    value = json.loads(secret)
    if (not isinstance(value, dict) or set(value) != {"username", "password"}
            or not all(isinstance(v, str) and not any(c in v for c in "\r\n\0")
                       for v in value.values()) or not value["username"]):
        raise ValueError("FTP credentials must contain username and password strings")
    # INI readers trim surrounding whitespace from usernames. Refuse rather than
    # silently logging in as somebody else; passwords are obscured before writing.
    if value["username"] != value["username"].strip():
        raise ValueError("FTP username cannot start or end with whitespace")
    return value


def prompt_ftp_credentials(drive: Drive) -> str:
    while True:
        log(f"FTP credentials for '{drive.name}' (Enter username for anonymous login).")
        if drive.hint:
            hint_msg(escape(drive.hint))
        username = getpass.getpass("FTP username [anonymous]: ") or "anonymous"
        password = getpass.getpass("FTP password [empty allowed]: ")
        secret = json.dumps({"username": username, "password": password})
        try:
            ftp_credentials(secret)
        except ValueError as exc:
            err(str(exc))
            continue
        return secret


def ftp_paths(drive: Drive) -> tuple[Path, Path]:
    runtime = get_runtime_dir()
    return runtime / f"ftp-{drive.name}.conf", runtime / f"ftp-{drive.name}.sock"


def ftp_unit(drive: Drive) -> str:
    return f"drive-manager-ftp-{drive.name}.service"


class FTPService(NamedTuple):
    pid: int
    state: str

    @property
    def running(self) -> bool:
        return self.pid != 0 or self.state not in {"inactive", "failed"}


def ftp_service(drive: Drive) -> FTPService:
    res = run(["systemctl", "--user", "show", ftp_unit(drive),
               "-p", "LoadState", "-p", "ActiveState", "-p", "MainPID"], timeout=5)
    properties = dict(line.split("=", 1) for line in res.out.splitlines() if "=" in line)
    if properties.get("LoadState") == "not-found":
        return FTPService(0, "inactive")
    if (not res.ok or not properties.get("MainPID", "").isdigit()
            or not properties.get("ActiveState")):
        raise ValueError("cannot query FTP user service; runtime files retained, check systemctl --user")
    return FTPService(int(properties["MainPID"]), properties["ActiveState"])


def ftp_remember(drive: Drive, entry: MountEntry) -> None:
    """Record exact kernel mount identity for normal recovery after a process crash."""
    config, _ = ftp_paths(drive)
    record = config.with_suffix(".json")
    temporary = config.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump({"mount_id": entry.mount_id, "majmin": entry.majmin}, stream)
    os.replace(temporary, record)


def ftp_matches(drive: Drive, entry: MountEntry | None, *, service: FTPService | None = None,
                stopped: bool = False) -> bool:
    """Endpoint/live command identity, plus kernel identity when mounted."""
    if entry is not None and (entry.fstype != "fuse.rclone" or entry.source != f"{drive.remote}:" or entry.root != "/"
            or f"user_id={os.getuid()}" not in entry.super_options.split(",")
            or "rw" not in entry.options.split(",")):
        return False
    config, _ = ftp_paths(drive)
    try:
        service = service if service is not None else ftp_service(drive)
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(config)
        section = parser[drive.remote]
        if not (section["type"] == "ftp" and section["host"] == drive.host
                and section.getint("port") == drive.port):
            return False
        if not service.running:
            if not stopped or entry is None:
                return False
            record = json.loads(config.with_suffix(".json").read_text(encoding="utf-8"))
            return record == {"mount_id": entry.mount_id, "majmin": entry.majmin}
        proc = Path(f"/proc/{service.pid}")
        argv = (proc / "cmdline").read_bytes().decode().rstrip("\0").split("\0")
        return (proc.stat().st_uid == os.getuid() and len(argv) >= 6
                and Path(argv[0]).name == "rclone"
                and argv[1:4] == ["mount", f"{drive.remote}:", str(drive.mountpoint)]
                and argv[4:6] == ["--config", str(config)])
    except (OSError, UnicodeError, KeyError, ValueError, configparser.Error):
        return False


def ftp_stats(drive: Drive) -> dict:
    _, sock = ftp_paths(drive)
    res = run(["rclone", "rc", "--unix-socket", str(sock), "vfs/stats"], timeout=5)
    if not res.ok:
        raise ValueError("VFS status unavailable; mount retained")
    value = json.loads(res.out)
    cache = value.get("diskCache") if isinstance(value, dict) else None
    if (not isinstance(cache, dict) or value.get("fs") != f"{drive.remote}:"
            or not all(type(cache.get(k)) is int and cache[k] >= 0 for k in
                       ("uploadsQueued", "uploadsInProgress", "erroredFiles"))):
        raise ValueError("VFS status incomplete or identity mismatch; mount retained")
    return cache


def ftp_cleanup(drive: Drive) -> None:
    config, sock = ftp_paths(drive)
    for path in (config, sock, config.with_suffix(".json"), config.with_suffix(".tmp")):
        path.unlink(missing_ok=True)


def ftp_detach(drive: Drive, entry: MountEntry) -> None:
    if mount_entry_for(drive.mountpoint) != entry:
        raise ValueError("mount changed during teardown; refusing to unmount it")
    res = run(["fusermount3", "-u", str(drive.mountpoint)], timeout=10)
    if not res.ok or mount_entry_for(drive.mountpoint) is not None:
        raise ValueError("unmount failed (close open files and retry); service retained")


def ftp_unlock(drive: Drive, secret: str | None) -> Outcome:
    try:
        service = ftp_service(drive)
        occupant = mount_entry_for(drive.mountpoint)
        if occupant is not None:
            if ftp_matches(drive, occupant, service=service):
                ftp_remember(drive, occupant)
                success(f"'{drive.name}' is already mounted.")
                return Outcome.OK if run_hooks(drive, "post_unlock", drive.post_unlock) else Outcome.FAILED
            if not ftp_matches(drive, occupant, service=service, stopped=True):
                raise ValueError("foreign or unverified mount; inspect before retrying")
            log(f"'{drive.name}': recovering recorded mount after service exit; cache retained.")
            ftp_detach(drive, occupant)
        if service.running:
            raise ValueError("service still running without the expected mount; lock it first")
        if secret is None:
            return Outcome.NEEDS_PASSPHRASE
        credentials = ftp_credentials(secret)
        target = drive.mountpoint
        if target.is_symlink():
            raise ValueError("mountpoint is a symlink")
        target.mkdir(parents=True, exist_ok=True)
        if any(target.iterdir()):
            raise ValueError("mountpoint contains local files")
        config, sock = ftp_paths(drive)
        # Even anonymous/empty passwords need a valid rclone-obscured string.
        obscured = run(["rclone", "obscure", "-"],
                       stdin=(credentials["password"] + "\n").encode(), timeout=5)
        if not obscured.ok or not obscured.out.strip():
            raise ValueError("rclone password encoding failed")
        parser = configparser.ConfigParser(interpolation=None)
        parser[drive.remote] = {"type": "ftp", "host": drive.host, "port": str(drive.port),
                                "user": credentials["username"], "pass": obscured.out.strip()}
        fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            parser.write(stream)
        sock.unlink(missing_ok=True)
        config.with_suffix(".json").unlink(missing_ok=True)
        cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "rclone"
        res = run(["systemd-run", "--user", "--collect", "--quiet",
                   f"--unit={ftp_unit(drive)}", "--property=TimeoutStopSec=15s",
                   "--property=SendSIGKILL=no", shutil.which("rclone"), "mount", f"{drive.remote}:",
                   str(target), "--config", str(config), "--cache-dir", str(cache),
                   # FTP LIST can leave size/mtime unchanged for external edits.
                   # Bypass kernel data caching; retain VFS caching for writes/retries.
                   "--direct-io", "--vfs-cache-mode", "writes", "--vfs-read-chunk-size", "0",
                   # FTP may lack usable modification times for fingerprints.
                   # Retain pending writes, but promptly evict closed, clean data.
                   "--vfs-handle-caching", "0s", "--vfs-cache-max-age", "0s",
                   "--vfs-cache-poll-interval", "1s",
                   # Zero selects synchronous uploads and bypasses retry queuing.
                   "--vfs-write-back", "1ms", "--dir-cache-time", "1s", "--poll-interval", "0",
                   "--rc", "--rc-addr", f"unix://{sock}"], timeout=10)
        if not res.ok:
            raise ValueError(f"user-service start failed; inspect journalctl --user -u {ftp_unit(drive)}")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            service = ftp_service(drive)
            if not service.running:
                raise ValueError(f"FTP service exited; check endpoint/login and journalctl --user -u {ftp_unit(drive)}")
            entry = mount_entry_for(target)
            if entry is not None and ftp_matches(drive, entry, service=service):
                ftp_remember(drive, entry)
                try:
                    ftp_stats(drive)  # Socket/VFS initialization can lag behind FUSE.
                except ValueError:
                    time.sleep(0.2)
                    continue
                success(f"'{drive.name}' mounted at {target}.")
                return Outcome.OK if run_hooks(drive, "post_unlock", drive.post_unlock) else Outcome.FAILED
            time.sleep(0.2)
        raise ValueError("mount startup timed out; inspect service and use lock before retrying")
    except (OSError, ValueError, configparser.Error) as exc:
        err(f"'{drive.name}': {escape(str(exc))}")
        try:
            if not ftp_service(drive).running and mount_entry_for(drive.mountpoint) is None:
                ftp_cleanup(drive)
        except (OSError, ValueError):
            pass  # Unknown service state: retain runtime material until verified stopped.
        return Outcome.FAILED


def ftp_cache_dirty(drive: Drive) -> bool:
    """Inspect retained writes while allowing rclone to evict clean cache entries."""
    cache_home = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "rclone"
    metadata = cache_home / "vfsMeta" / drive.remote
    try:
        if not stat.S_ISDIR(metadata.stat().st_mode):
            raise ValueError("cache metadata path is not a directory")
    except FileNotFoundError:
        return False

    def onerror(exc: OSError) -> None:
        if not isinstance(exc, FileNotFoundError):
            raise exc

    for root, _, files in os.walk(metadata, onerror=onerror):
        for name in files:
            try:
                value = json.loads((Path(root) / name).read_text(encoding="utf-8"))
            except FileNotFoundError:
                # The cleaner may remove an uploaded file after os.walk lists it.
                # Continue checking the remaining entries for unsent writes.
                continue
            if not isinstance(value, dict) or type(value.get("Dirty")) is not bool:
                raise ValueError("cannot verify retained cache metadata; inspect before retrying")
            if value["Dirty"]:
                return True
    return False


def ftp_lock(drive: Drive) -> Outcome:
    try:
        service = ftp_service(drive)
        entry = mount_entry_for(drive.mountpoint)
        if (entry is not None or service.running) and not ftp_matches(drive, entry, service=service, stopped=True):
            raise ValueError("mount identity mismatch; refusing teardown")
        if not run_hooks(drive, "pre_lock", drive.pre_lock):
            return Outcome.FAILED
        # No mount means no application can be writing through it. A verified
        # service stuck before VFS initialization can be stopped without RC; disk
        # cache is still checked and retained after it exits.
        if service.running and entry is not None:
            deadline = time.monotonic() + 30
            while True:
                cache = ftp_stats(drive)
                if not any(cache[k] for k in ("uploadsQueued", "uploadsInProgress", "erroredFiles")):
                    break
                if time.monotonic() >= deadline:
                    raise ValueError("pending/failed uploads; mount and cache retained, retry after upload completes")
                time.sleep(0.5)
        if service.running and entry is not None and ftp_cache_dirty(drive):
            raise ValueError("unsent cache data remains; mount retained, wait for upload or recover before locking")
        if entry is not None:
            # Busy files leave a working mount/service. Never force or lazily detach.
            ftp_detach(drive, entry)
        run(["systemctl", "--user", "stop", ftp_unit(drive)], timeout=20)
        if ftp_service(drive).running or mount_entry_for(drive.mountpoint) is not None:
            raise ValueError("teardown incomplete; runtime config and cache retained")
        if ftp_cache_dirty(drive):
            raise ValueError("unmounted with unsent cache data; unlock to resume uploads")
        ftp_cleanup(drive)
        success(f"'{drive.name}' unmounted.")
        return Outcome.OK if run_hooks(drive, "post_lock", drive.post_lock) else Outcome.FAILED
    except (OSError, ValueError) as exc:
        err(f"'{drive.name}': {escape(str(exc))}")
        return Outcome.FAILED


def show_status(drives: dict[str, Drive]) -> None:
    snapshot = read_mountinfo()  # one mount-table read for the whole fleet
    table = Table(show_header=True, header_style="bold white", border_style="bright_black")
    table.add_column("DRIVE", width=14)
    table.add_column("TYPE", width=10)
    table.add_column("FS", width=8)
    table.add_column("CRYPT", width=8)
    table.add_column("STATUS", width=11)
    table.add_column("MOUNTPOINT")
    for name, drive in sorted(drives.items()):
        if drive.type is DriveType.FTP:
            entry = mount_entry_for(drive.mountpoint, snapshot)
            state = "Unmounted" if entry is None else ("Mounted" if ftp_matches(drive, entry) else "Conflict")
            table.add_row(name, drive.type, "ftp", "—", state, str(drive.mountpoint))
            continue
        outer = resolve_device(drive.outer_uuid) if drive.type is DriveType.PROTECTED else None
        mapping = find_crypt_mapping(drive, outer) if drive.type is DriveType.PROTECTED else None
        fs_dev = resolve_device(drive.fs_uuid)
        if fs_dev is None and mapping is not None:
            fs_dev = mapping.dm_node
        mounts = mounts_for_device(fs_dev, snapshot)
        fstype = probe_fstype(fs_dev) or normalize_fstype(drive.fstype) or "?"
        crypt = "—"
        if drive.type is DriveType.PROTECTED:
            if outer is None:
                crypt = "[dim]absent[/]"
            else:
                crypt = "[green]open[/]" if mapping else "[red]closed[/]"
        mp = drive.mountpoint
        occupant = mount_entry_for(mp, snapshot)
        if occupant is not None and not entry_matches(occupant, fs_dev, fstype if fstype != "?" else None):
            table.add_row(f"[bold yellow]▲[/] {name}", drive.type, fstype, crypt, "[bold yellow]Conflict[/]",
                          f"{mp} holds {occupant.fstype} (expected {fstype})")
        elif mp in mounts:
            table.add_row(f"[bold green]●[/] {name}", drive.type, fstype, crypt, "[bold green]Mounted[/]", str(mp))
        elif mounts:
            table.add_row(f"[bold yellow]▲[/] {name}", drive.type, fstype, crypt, "[bold yellow]Divergent[/]",
                          f"{', '.join(map(str, mounts))} (expected {mp})")
        else:
            table.add_row(f"[bold red]○[/] {name}", drive.type, fstype, crypt, "[bold red]Unmounted[/]", str(mp))
    with print_lock:
        console.print()
        console.print(table)
        console.print()


def do_unlock(drive: Drive, secret: str | None, *, interactive: bool, all_mountpoints: set[Path]) -> Outcome:
    """interactive=True only on the main thread. Workers get interactive=False and may return NEEDS_PASSPHRASE."""
    if drive.type is DriveType.FTP:
        outcome = ftp_unlock(drive, secret)
        if outcome is Outcome.NEEDS_PASSPHRASE and interactive:
            secret = prompt_ftp_credentials(drive)
            outcome = ftp_unlock(drive, secret)
            if outcome is Outcome.OK:
                keyring_set(ftp_account(drive), secret)
        return outcome
    log(f"Unlock sequence for '{drive.name}' started.")
    target = drive.mountpoint

    # --- Step 1: mount-table reconciliation -------------------------------------------------
    snapshot = read_mountinfo()
    fs_dev = resolve_device(drive.fs_uuid)
    mounts = mounts_for_device(fs_dev, snapshot)
    expected = normalize_fstype(drive.fstype) or normalize_fstype(probe_fstype(fs_dev))
    occupant = mount_entry_for(target, snapshot)
    if occupant is not None and not entry_matches(occupant, fs_dev, expected):
        err(f"'{drive.name}': target {escape(str(target))} holds an unrelated {occupant.fstype} mount; refusing to replace it.")
        return Outcome.FAILED
    if target in mounts:
        for stale in (m for m in mounts if m != target):
            log(f"Removing redundant stale mount at {escape(str(stale))}...")
            if unmount_path(stale, fs_dev, expected, interactive=interactive):
                prune_stale_dir(stale, all_mountpoints)
            else:
                return Outcome.FAILED
        if not reconcile_integrations(drive):
            return Outcome.FAILED
        success(f"'{drive.name}' is already mounted at {escape(str(target))}.")
        return Outcome.OK if run_hooks(drive, "post_unlock", drive.post_unlock) else Outcome.FAILED
    for stale in mounts:
        log(f"'{drive.name}' is mounted at divergent path {escape(str(stale))}; relocating to {escape(str(target))}...")
        if not unmount_path(stale, fs_dev, expected, interactive=interactive):
            err(f"Cannot relocate '{drive.name}': divergent mount {escape(str(stale))} is still busy.")
            return Outcome.FAILED
        prune_stale_dir(stale, all_mountpoints)

    # --- Step 2: crypt container --------------------------------------------------------------
    mapping: CryptMapping | None = None
    opened_here = False
    if drive.type is DriveType.PROTECTED:
        outer_dev = resolve_device(drive.outer_uuid)
        if outer_dev is None:
            err(f"Physical device for '{drive.name}' not present (outer UUID {drive.outer_uuid}).")
            hint_msg("Is it plugged in? Check: lsblk -o NAME,FSTYPE,UUID")
            return Outcome.FAILED
        mapping = find_crypt_mapping(drive, outer_dev)
        if mapping and mapping_is_live(mapping, outer_dev):
            log(f"Crypt container for '{drive.name}' is already open as /dev/mapper/{mapping.name}.")
        else:
            if mapping:
                warn(f"Stale/unresponsive mapping /dev/mapper/{mapping.name}; closing before re-open...")
                if not close_container(mapping.name, report=True):
                    err(f"Cannot close stale mapping {mapping.name}.")
                    crypt_forensics(mapping)
                    return Outcome.FAILED
                mapping = None
            prompted_here = False
            while True:
                if secret is None:
                    if not interactive:
                        return Outcome.NEEDS_PASSPHRASE
                    secret = prompt_passphrase(drive)
                    prompted_here = True
                log(f"Opening container for '{drive.name}'...")
                if open_container(drive, outer_dev, secret):
                    clear_attempts(drive.name)
                    opened_here = True
                    break
                record_failed_attempt(drive.name, secret)
                secret = None
                if not interactive:
                    return Outcome.NEEDS_PASSPHRASE
            if prompted_here and secret is not None and keyring_set(drive.name, secret):
                success(f"Passphrase for '{drive.name}' saved to the keyring.")
            secret = None
            fs_dev = wait_for_device(drive.inner_uuid or "")
            mapping = find_crypt_mapping(drive, outer_dev)
            if fs_dev is None:
                if mapping is None:
                    err(f"Container opened but no mapping found for '{drive.name}' — udev/dm inconsistency.")
                    hint_msg(f"Check: lsblk --tree {outer_dev} ; sudo dmsetup ls --target crypt")
                    return Outcome.FAILED
                hint_msg(f"udev did not publish /dev/disk/by-uuid/{drive.inner_uuid}; mounting via /dev/mapper/{mapping.name}.")
                if probe_fstype(mapping.dm_node) is None:
                    err(f"Container opened but no filesystem detected on /dev/mapper/{mapping.name}.")
                    hint_msg(f"Check inner_uuid in drives.toml: lsblk -f /dev/mapper/{mapping.name}")
                    close_container(mapping.name, report=False)
                    return Outcome.FAILED
            elif mapping is not None and device_majmin(fs_dev) != device_majmin(mapping.dm_node):
                err(f"'{drive.name}': inner UUID resolves to a different device than /dev/mapper/{mapping.name}; refusing.")
                if opened_here:
                    close_container(mapping.name, report=False)
                return Outcome.FAILED

    # --- Step 3: mount --------------------------------------------------------------------
    if fs_dev is not None:
        source, source_dev = f"UUID={drive.fs_uuid}", fs_dev
    elif mapping is not None:
        source, source_dev = f"/dev/mapper/{mapping.name}", mapping.dm_node
    else:
        err(f"Filesystem UUID {drive.fs_uuid} for '{drive.name}' not present.")
        hint_msg("Check: ls -l /dev/disk/by-uuid/ ; lsblk -f")
        return Outcome.FAILED
    fstype = normalize_fstype(drive.fstype) or normalize_fstype(probe_fstype(source_dev))
    rotational = is_rotational(source_dev)
    audit_mount_options(drive, fstype, rotational)
    argv = build_mount_argv(drive, source, fstype)
    if mount_entry_for(target) is not None:
        err(f"'{drive.name}': target became occupied before mount; refusing an overmount.")
        if opened_here and mapping is not None:
            close_container(mapping.name, report=False)
        return Outcome.FAILED
    log(f"Mounting '{drive.name}' ({fstype or 'auto'}, {'HDD' if rotational else 'SSD/NVMe'}) at {escape(str(target))}...")
    res = sudo(argv, report=False)
    if not res.ok:
        err(f"mount failed for '{drive.name}' (rc={res.rc}): {escape(res.errtxt.strip())}")
        if is_ntfs_fstype(fstype):
            ntd = kernel_ntfs_driver()
            hint_msg(f"Next: sudo dmesg | tail -n 20 ; lsblk -f {source_dev} ; driver used: {ntd} "
                     f"(/proc/filesystems). Dirty/hibernated NTFS needs Windows chkdsk + full shutdown; never use 'force'.")
        else:
            hint_msg(f"Next: sudo dmesg | tail -n 20 ; lsblk -f {source_dev} ; check mount_options for {fstype or 'this fs'}")
        if opened_here and mapping is not None:
            close_container(mapping.name, report=False)
        return Outcome.FAILED
    mounted = mount_entry_for(target)
    if mounted is None or not entry_matches(mounted, source_dev, fstype):
        err(f"'{drive.name}': mount reported success but target verification failed; inspect manually.")
        return Outcome.FAILED
    success(f"'{drive.name}' mounted at {escape(str(target))}.")
    if not reconcile_integrations(drive):
        return Outcome.FAILED
    dispatch_trim(drive, source_dev, fstype, rotational, mounted)
    if not run_hooks(drive, "post_unlock", drive.post_unlock):
        return Outcome.FAILED
    return Outcome.OK


def do_lock(drive: Drive, *, interactive: bool, all_mountpoints: set[Path]) -> Outcome:
    if drive.type is DriveType.FTP:
        return ftp_lock(drive)
    log(f"Lock sequence for '{drive.name}' started.")
    if not run_hooks(drive, "pre_lock", drive.pre_lock):
        err(f"Aborting lock of '{drive.name}': pre_lock hook failed; filesystem remains mounted.")
        return Outcome.FAILED
    stop_trim(drive)  # best-effort: a queued TRIM must not run on a later fs at this path

    # --- Step 1: unmount everywhere ----------------------------------------------------------
    snapshot = read_mountinfo()
    fs_dev = resolve_device(drive.fs_uuid)
    mounts = set(mounts_for_device(fs_dev, snapshot))
    expected = normalize_fstype(drive.fstype) or normalize_fstype(probe_fstype(fs_dev))
    occupant = mount_entry_for(drive.mountpoint, snapshot)
    if occupant is not None and not entry_matches(occupant, fs_dev, expected):
        if not mounts:
            err(f"'{drive.name}': target holds an unrelated {occupant.fstype} mount; refusing to unmount it.")
            return Outcome.FAILED
    elif occupant is not None:
        mounts.add(drive.mountpoint.resolve())
    if mounts:
        for mp in sorted(mounts, reverse=True):   # deepest first
            if not unmount_path(mp, fs_dev, expected, interactive=interactive):
                err(f"Aborting lock of '{drive.name}': {escape(str(mp))} could not be unmounted.")
                return Outcome.FAILED
            prune_stale_dir(mp, all_mountpoints)
        log(f"All mountpoints of '{drive.name}' unmounted.")
    else:
        log(f"'{drive.name}' is not mounted.")

    if drive.type is DriveType.SIMPLE:
        success(f"'{drive.name}' released.")
        return Outcome.OK if run_hooks(drive, "post_lock", drive.post_lock) else Outcome.FAILED

    # --- Step 2: close crypt container -------------------------------------------------------
    outer_dev = resolve_device(drive.outer_uuid)
    mapping = find_crypt_mapping(drive, outer_dev)
    if mapping is None:
        if outer_dev is None:
            success(f"'{drive.name}' is physically absent and no mapping remains.")
        else:
            success(f"Container of '{drive.name}' is already locked.")
        return Outcome.OK if run_hooks(drive, "post_lock", drive.post_lock) else Outcome.FAILED
    if outer_dev is None:
        warn(f"Physical device gone but ghost mapping /dev/mapper/{mapping.name} remains; forcing teardown.")

    sudo(["blockdev", "--flushbufs", str(mapping.dm_node)], report=False)
    log(f"Closing crypt node {mapping.name}...")
    if close_container(mapping.name):
        success(f"'{drive.name}' locked.")
        return Outcome.OK if run_hooks(drive, "post_lock", drive.post_lock) else Outcome.FAILED
    for attempt in range(1, LOCK_MAX_RETRIES + 1):
        time.sleep(LOCK_RETRY_DELAY)
        if close_container(mapping.name):
            success(f"'{drive.name}' locked (attempt {attempt + 1}).")
            return Outcome.OK if run_hooks(drive, "post_lock", drive.post_lock) else Outcome.FAILED
        log(f"Close attempt {attempt}/{LOCK_MAX_RETRIES} for '{drive.name}' failed; retrying...")
    log(f"'{drive.name}' is still held; requesting deferred close (kernel removes it when the last opener exits)...")
    if close_container(mapping.name, deferred=True):
        err(f"'{drive.name}' is only DEFERRED-closed (still visible until holders exit); returning failure. "
            f"Verify later with: sudo cryptsetup status {mapping.name}")
        return Outcome.FAILED
    err(f"All close strategies failed for {mapping.name}.")
    crypt_forensics(mapping)
    return Outcome.FAILED


# ------------------------------------------------------------------------------
#  PIPELINES (prompts only on the main thread)
# ------------------------------------------------------------------------------
def _container_needs_secret(drive: Drive) -> bool:
    if drive.type is DriveType.FTP:
        entry = mount_entry_for(drive.mountpoint)
        return entry is None or not ftp_matches(drive, entry)
    if drive.type is not DriveType.PROTECTED:
        return False
    if drive.mountpoint in mounts_for_device(resolve_device(drive.fs_uuid)):
        return False
    outer = resolve_device(drive.outer_uuid)
    if outer is None:
        return False   # do_unlock will report the missing device
    mapping = find_crypt_mapping(drive, outer)
    return not (mapping and mapping_is_live(mapping, outer))


def unlock_pipeline(drives: dict[str, Drive], targets: list[str]) -> int:
    all_mps = {d.mountpoint for d in drives.values()}
    secrets: dict[str, str | None] = {}
    prompted: set[str] = set()

    # Phase 1 — sequential credential collection (main thread)
    for name in targets:
        drive = drives[name]
        secrets[name] = None
        if not _container_needs_secret(drive):
            continue
        if drive.type is DriveType.FTP:
            secret = keyring_get(ftp_account(drive))
            if secret is not None:
                try:
                    ftp_credentials(secret)
                except ValueError:
                    warn(f"'{name}': invalid saved FTP credentials; prompting again.")
                    secret = None
            if secret is None:
                secret = prompt_ftp_credentials(drive)
                prompted.add(name)
            secrets[name] = secret
            continue
        secret = keyring_get(drive.name)
        if secret is None:
            log(f"No keyring entry for '[bold cyan]{escape(drive.name)}[/]'.")
            secret = prompt_passphrase(drive)
            prompted.add(name)
        secrets[name] = secret

    # Phase 2 — parallel unlock+mount (no prompts inside workers)
    outcomes: dict[str, Outcome] = {}
    if len(targets) == 1:
        name = targets[0]
        outcomes[name] = do_unlock(drives[name], secrets[name], interactive=True, all_mountpoints=all_mps)
    else:
        log(f"Dispatching parallel unlock for {len(targets)} drives...")
        with ThreadPoolExecutor(max_workers=min(len(targets), MAX_PARALLEL), thread_name_prefix="unlock") as pool:
            futs = {pool.submit(do_unlock, drives[n], secrets[n], interactive=False, all_mountpoints=all_mps): n for n in targets}
            for fut in as_completed(futs):
                n = futs[fut]
                try:
                    outcomes[n] = fut.result()
                except Exception as e:  # noqa: BLE001 - worker bug must not take down siblings
                    err(f"Internal error unlocking '{n}': {escape(repr(e))}")
                    outcomes[n] = Outcome.FAILED
        # Phase 3 — finish anything that needs a human, sequentially
        for n in targets:
            if outcomes.get(n) is Outcome.NEEDS_PASSPHRASE:
                log(f"'{n}' needs interactive input; continuing on the main thread...")
                outcomes[n] = do_unlock(drives[n], None, interactive=True, all_mountpoints=all_mps)

    # Persist credentials typed in Phase 1 only after successful unlock.
    for n in prompted:
        account = ftp_account(drives[n]) if drives[n].type is DriveType.FTP else n
        if outcomes.get(n) is Outcome.OK and secrets.get(n) and keyring_set(account, secrets[n] or ""):
            success(f"Credentials for '{n}' saved to the keyring.")
    secrets.clear()

    failed = [n for n, o in outcomes.items() if o is not Outcome.OK]
    if failed:
        err(f"Unlock incomplete for: {', '.join(failed)}")
        return EXIT_FAIL
    return EXIT_OK


def lock_pipeline(drives: dict[str, Drive], targets: list[str]) -> int:
    all_mps = {d.mountpoint for d in drives.values()}
    outcomes: dict[str, Outcome] = {}
    if len(targets) == 1:
        outcomes[targets[0]] = do_lock(drives[targets[0]], interactive=True, all_mountpoints=all_mps)
    else:
        log(f"Dispatching parallel lock for {len(targets)} drives...")
        with ThreadPoolExecutor(max_workers=min(len(targets), MAX_PARALLEL), thread_name_prefix="lock") as pool:
            futs = {pool.submit(do_lock, drives[n], interactive=False, all_mountpoints=all_mps): n for n in targets}
            for fut in as_completed(futs):
                n = futs[fut]
                try:
                    outcomes[n] = fut.result()
                except Exception as e:  # noqa: BLE001
                    err(f"Internal error locking '{n}': {escape(repr(e))}")
                    outcomes[n] = Outcome.FAILED
        for n in targets:
            if drives[n].type is not DriveType.FTP and outcomes.get(n) is Outcome.FAILED and mounts_for_device(resolve_device(drives[n].fs_uuid)):
                log(f"'{n}' still mounted after parallel pass; retrying interactively on the main thread...")
                outcomes[n] = do_lock(drives[n], interactive=True, all_mountpoints=all_mps)
    failed = [n for n, o in outcomes.items() if o is not Outcome.OK]
    if failed:
        err(f"Lock incomplete for: {', '.join(failed)}")
        return EXIT_FAIL
    return EXIT_OK


def set_password(drives: dict[str, Drive], name: str) -> bool:
    drive = drives.get(name)
    if drive is None:
        err(f"Drive '{name}' is not in the configuration.")
        return False
    if drive.type is DriveType.FTP:
        secret = prompt_ftp_credentials(drive)
        if keyring_set(ftp_account(drive), secret):
            success(f"FTP username and password stored for '{name}'.")
            return True
        return False
    if drive.type is not DriveType.PROTECTED:
        err(f"'{name}' is a SIMPLE drive and has no passphrase.")
        return False
    console.print(Panel(f"Storing keyring passphrase for [bold cyan]{escape(name)}[/] "
                        f"(service '{KEYRING_SERVICE}', account '{escape(name)}').",
                        title="Keyring Setup", border_style="cyan"))
    try:
        pwd = getpass.getpass(f"Passphrase for '{name}': ")
        confirm = getpass.getpass("Confirm: ")
    except (KeyboardInterrupt, EOFError):
        cancel_exit()
        return False
    if not pwd:
        err("Empty passphrase refused.")
        return False
    if pwd != confirm:
        err("Passphrases do not match.")
        return False
    if keyring_set(name, pwd):
        clear_attempts(name)
        success(f"Passphrase stored for '{name}'.")
        return True
    return False


# ------------------------------------------------------------------------------
#  CONFIG: strict schema validation + path normalization
# ------------------------------------------------------------------------------
UUID_PATTERNS: Final = (
    re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),  # ext4/btrfs/xfs/LUKS (lowercase)
    re.compile(r"^[0-9A-F]{16}$"),                                                    # NTFS (uppercase)
    re.compile(r"^[0-9A-F]{4}-[0-9A-F]{4}$"),                                        # vfat/exFAT (uppercase)
)
ALLOWED_KEYS: Final = frozenset({
    "type", "mountpoint", "outer_uuid", "inner_uuid", "hint",
    "fstype", "mount_options", "symlinks",
    "post_unlock", "pre_lock", "post_lock", "host", "port", "remote",
})
NAME_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class ConfigError(ValueError):
    pass


def resolve_configured_path(raw: str, *, resolve_symlinks: bool) -> Path:
    """Expands $VARS and ~, rebases legacy /home/<olduser>/... onto the active $HOME."""
    expanded = os.path.expandvars(raw.strip())
    p = Path(expanded).expanduser()
    if not p.is_absolute():
        raise ConfigError(f"path must be absolute after expansion: '{raw}'")
    home = Path.home()
    parts = p.parts
    if len(parts) > 2 and parts[1] == "home" and parts[2] != home.name:
        p = home.joinpath(*parts[3:])
    return p.resolve() if resolve_symlinks else p.absolute()


def _validate_uuid(field_name: str, value: object) -> str:
    if not isinstance(value, str) or not any(rx.match(value) for rx in UUID_PATTERNS):
        raise ConfigError(f"{field_name}='{value}' is not a valid blkid UUID "
                          "(lowercase 8-4-4-4-12, uppercase 16-hex NTFS, or uppercase XXXX-XXXX FAT). "
                          "Case matters: /dev/disk/by-uuid is case-sensitive.")
    return value


def parse_drive(name: str, data: object) -> Drive:
    if not NAME_RE.match(name):
        raise ConfigError("drive name must match [A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
    if not isinstance(data, dict):
        raise ConfigError("drive entry must be a table")
    unknown = set(data) - ALLOWED_KEYS
    if unknown:
        raise ConfigError(f"unknown key(s): {', '.join(sorted(unknown))}")
    for key in ("type", "mountpoint"):
        if key not in data:
            raise ConfigError(f"missing required key '{key}'")
    try:
        dtype = DriveType(str(data["type"]).upper())
    except ValueError:
        raise ConfigError(f"type must be PROTECTED, SIMPLE or FTP, got '{data['type']}'") from None
    if dtype is DriveType.FTP:
        forbidden = set(data) & {"outer_uuid", "inner_uuid", "fstype", "mount_options", "symlinks"}
        if forbidden:
            raise ConfigError(f"FTP does not accept: {', '.join(sorted(forbidden))}")
        host = data.get("host")
        if not isinstance(host, str) or not host or any(c.isspace() or c in "/@\\\0" for c in host):
            raise ConfigError("FTP host must be a hostname/IP without URL, path or credentials")
        port = data.get("port", 21)
        if type(port) is not int or not 1 <= port <= 65535:
            raise ConfigError("FTP port must be an integer from 1 to 65535")
        remote = data.get("remote", name)
        if not isinstance(remote, str) or not NAME_RE.fullmatch(remote):
            raise ConfigError("FTP remote must use drive-name syntax")
        outer = None
    else:
        if set(data) & {"host", "port", "remote"}:
            raise ConfigError("host, port and remote are only valid for FTP")
        if "outer_uuid" not in data:
            raise ConfigError("missing required key 'outer_uuid'")
        outer = _validate_uuid("outer_uuid", data["outer_uuid"])
    inner = data.get("inner_uuid")
    if dtype is DriveType.PROTECTED:
        if inner is None:
            raise ConfigError("PROTECTED drives require inner_uuid")
        inner = _validate_uuid("inner_uuid", inner)
        if inner == outer:
            raise ConfigError("inner_uuid must differ from outer_uuid")
    elif inner is not None:
        raise ConfigError("inner_uuid is only valid for PROTECTED drives")
    for key in ("hint", "fstype"):
        if key in data and not isinstance(data[key], str):
            raise ConfigError(f"{key} must be a string")
    fstype_raw = data.get("fstype")
    if isinstance(fstype_raw, str) and fstype_raw.strip().lower() == "ntfs3":
        raise ConfigError('fstype "ntfs3" is retired; use "ntfs" (7.1+ in-kernel driver)')
    fstype = normalize_fstype(fstype_raw) if isinstance(fstype_raw, str) else None
    opts = data.get("mount_options", [])
    if not isinstance(opts, list) or not all(isinstance(o, str) and o and "," not in o and " " not in o for o in opts):
        raise ConfigError("mount_options must be a list of single, non-empty option strings")
    if "force" in opts:
        raise ConfigError("'force' is forbidden (clears the NTFS dirty bit -> data loss under Windows)")
    links = data.get("symlinks", [])
    if not isinstance(links, list) or not all(isinstance(s, str) and s for s in links):
        raise ConfigError("symlinks must be a list of non-empty strings")
    if not isinstance(data["mountpoint"], str) or not data["mountpoint"]:
        raise ConfigError("mountpoint must be a non-empty string")
    mountpoint = resolve_configured_path(data["mountpoint"], resolve_symlinks=dtype is not DriveType.FTP)
    if dtype is DriveType.FTP:
        mountpoint = Path(os.path.normpath(mountpoint))
        mountpoint = mountpoint.parent.resolve() / mountpoint.name
    if mountpoint == Path("/") or mountpoint == Path.home():
        raise ConfigError("mountpoint may not be / or $HOME")
    symlinks = tuple(resolve_configured_path(s, resolve_symlinks=False) for s in links)
    if mountpoint in symlinks:
        raise ConfigError("a symlink cannot point to itself (symlink == mountpoint)")

    # Lifecycle hooks validation
    hooks: dict[str, tuple[str, ...]] = {}
    for hook_key in ("post_unlock", "pre_lock", "post_lock"):
        val = data.get(hook_key, [])
        if isinstance(val, str):
            val = [val] if val.strip() else []
        if not isinstance(val, list) or not all(isinstance(c, str) and c.strip() for c in val):
            raise ConfigError(f"{hook_key} must be a string or list of command strings")
        hooks[hook_key] = tuple(c.strip() for c in val)

    return Drive(
        name, dtype, mountpoint, outer, inner, data.get("hint"), fstype, tuple(opts), symlinks,
        post_unlock=hooks["post_unlock"], pre_lock=hooks["pre_lock"], post_lock=hooks["post_lock"],
        host=data.get("host"), port=data.get("port", 21), remote=data.get("remote", name),
    )


def load_config(override: Path | None) -> dict[str, Drive]:
    if override is not None:
        if not override.is_file():
            err(f"Config file '{override}' not found.")
            sys.exit(EXIT_FAIL)
        target = override
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
        base = Path(xdg) if xdg else Path.home() / ".config"
        candidates = [base / "drive_manager" / "drives.toml", Path(__file__).resolve().parent / "drives.toml"]
        target = next((p for p in candidates if p.is_file()), None)
        if target is None:
            err("drives.toml not found. Searched: " + " , ".join(map(str, candidates)))
            sys.exit(EXIT_FAIL)
    try:
        with open(target, "rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        err(f"TOML parse error in {target}: {e}")
        sys.exit(EXIT_FAIL)
    except OSError as e:
        err(f"Cannot read {target}: {e.strerror}")
        sys.exit(EXIT_FAIL)
    top_unknown = set(raw) - {"drives"}
    if top_unknown:
        err(f"Config error: unknown top-level key(s) {', '.join(sorted(top_unknown))} in {target}")
        sys.exit(EXIT_FAIL)
    entries = raw.get("drives")
    if not isinstance(entries, dict) or not entries:
        err(f"Config error: no [drives.<name>] tables in {target}")
        sys.exit(EXIT_FAIL)
    drives: dict[str, Drive] = {}
    for name, data in entries.items():
        try:
            drives[name] = parse_drive(name, data)
        except ConfigError as e:
            err(f"Config error in [drives.{name}]: {e}")
            sys.exit(EXIT_FAIL)
    # cross-drive collisions (UUIDs across outer/inner, mountpoint overlaps, symlinks)
    seen: dict[str, str] = {}
    mountpoints: dict[Path, str] = {}
    remotes: set[str] = set()
    for d in drives.values():
        if d.type is DriveType.FTP:
            if d.remote in remotes:
                err("Config error: FTP remote/cache identity must be unique")
                sys.exit(EXIT_FAIL)
            remotes.add(d.remote)
        for label, key in (("outer_uuid", d.outer_uuid), ("inner_uuid", d.inner_uuid),
                           *((f"symlink {s}", str(s)) for s in d.symlinks)):
            if key is None:
                continue
            namespace = "uuid" if label in {"outer_uuid", "inner_uuid"} else "symlink"
            k = f"{namespace}::{key}"
            if k in seen and seen[k] != d.name:
                err(f"Config error: {label} '{key}' is shared by drives '{seen[k]}' and '{d.name}'")
                sys.exit(EXIT_FAIL)
            seen[k] = d.name
        for other_mp, other_name in mountpoints.items():
            if d.mountpoint.is_relative_to(other_mp) or other_mp.is_relative_to(d.mountpoint):
                err(f"Config error: overlapping mountpoints of '{other_name}' and '{d.name}'")
                sys.exit(EXIT_FAIL)
        mountpoints[d.mountpoint] = d.name
        for s in d.symlinks:
            for other in drives.values():
                if other.mountpoint == s or other.mountpoint.is_relative_to(s):
                    err(f"Config error: symlink '{s}' of drive '{d.name}' collides with the mountpoint of '{other.name}'")
                    sys.exit(EXIT_FAIL)
    return drives


# ------------------------------------------------------------------------------
#  MAIN
# ------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="drive_manager.py",
        description="Universal Drive Manager — Platinum Hybrid Edition (parallel LUKS/BitLocker fleet control)",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("-c", "--config", type=Path, help="Path to drives.toml (overrides XDG lookup)")
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {VERSION}")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("status", help="Live fleet table (no privileges required)")
    p = sub.add_parser("unlock", help="Decrypt (if needed) and mount drive(s)")
    p.add_argument("targets", nargs="+", metavar="DRIVE")
    p = sub.add_parser("lock", help="Unmount everywhere and close container(s)")
    p.add_argument("targets", nargs="+", metavar="DRIVE")
    p = sub.add_parser("set-password", help="Store local passphrase or FTP username/password in Secret Service")
    p.add_argument("targets", nargs="+", metavar="DRIVE")
    return parser


def main() -> int:
    prevent_root_execution()
    args = build_parser().parse_args()
    drives = load_config(args.config)

    match args.action:
        case "status":
            show_status(drives)
            return EXIT_OK

        case "set-password":
            acquire_lock()
            ok = True
            for i, name in enumerate(args.targets):
                if i:
                    console.print("\n[dim]" + "-" * 60 + "[/dim]\n")
                if not set_password(drives, name):
                    ok = False
                    err(f"Keyring setup for '{name}' failed.")
            return EXIT_OK if ok else EXIT_FAIL

        case "unlock" | "lock" as action:
            unknown = [t for t in args.targets if t not in drives]
            if unknown:
                err(f"Unknown drive(s): {', '.join(unknown)}. Configured: {', '.join(sorted(drives))}")
                return EXIT_FAIL
            targets = list(dict.fromkeys(args.targets))   # de-duplicate, keep order
            local = any(drives[n].type is not DriveType.FTP for n in targets)
            if local:
                check_dependencies()
                Sudo.prime()
                Sudo.start_keepalive()
            if any(drives[n].type is DriveType.FTP for n in targets):
                missing = [c for c in ("rclone", "fusermount3", "systemctl", "systemd-run") if shutil.which(c) is None]
                if missing:
                    err(f"Missing FTP commands: {', '.join(missing)}")
                    return EXIT_FAIL
            acquire_lock()
            accel = CPUAccelerator() if local else nullcontext()

            def _on_signal(signum: int, _frame) -> None:
                if local:
                    accel.restore()
                sys.exit(128 + signum)

            signal.signal(signal.SIGTERM, _on_signal)
            signal.signal(signal.SIGHUP, _on_signal)
            with accel:
                if action == "unlock":
                    return unlock_pipeline(drives, targets)
                return lock_pipeline(drives, targets)

        case _:
            return EXIT_FAIL


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        cancel_exit()
