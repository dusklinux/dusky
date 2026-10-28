"""Temporary storage and memory budgeting for Dusky's Cargo setup scripts."""

import errno
import fcntl
import os
import re
import shutil
import signal
import tempfile
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from pathlib import Path

GIB = 1024 ** 3
RESERVE = GIB
_BUILD_LOCK: ContextVar[int | None] = ContextVar("dusky_build_lock", default=None)


def font_source_files() -> list[Path]:
    """Include the shared selective-font loader in binary fingerprints."""
    root = Path(__file__).resolve().parents[2] / "rust_shared"
    directory = root / "fontdb"
    files = [root / "ui_fonts.rs", directory / "Cargo.toml"]
    files.extend(sorted(path for path in (directory / "src").rglob("*") if path.is_file()))
    return files


def build_lock_fds() -> tuple[int, ...]:
    """Let compiler children retain serialization if the installer is killed."""
    fd = _BUILD_LOCK.get()
    return () if fd is None else (fd,)


def memory_bytes() -> tuple[int, int]:
    values = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            values[key] = int(value.split()[0]) * 1024
    total, available = values["MemTotal"], values["MemAvailable"]
    cgroup = next((line.partition("::")[2] for line in
                   Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines()
                   if line.startswith("0::")), "/")
    root = Path("/sys/fs/cgroup")
    group = root / cgroup.lstrip("/")
    for directory in (group, *group.parents):
        if not directory.is_relative_to(root):
            break
        try:
            limit = int((directory / "memory.max").read_text(encoding="utf-8"))
            current = int((directory / "memory.current").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        total = min(total, limit)
        available = min(available, max(0, limit - current))
    return total, available


def filesystem_type(path: Path) -> str | None:
    real = path.resolve()
    best = (0, None)
    for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
        before, separator, after = line.partition(" - ")
        if not separator:
            continue
        mount = Path(re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), before.split()[4]))
        if real.is_relative_to(mount) and len(str(mount)) >= best[0]:
            best = (len(str(mount)), after.split()[0])
    return best[1]


def usable(path: Path, needed: int, *, ram: bool) -> bool:
    try:
        kind = filesystem_type(path)
        if kind is None or (ram and kind != "tmpfs") or (not ram and kind in {"tmpfs", "ramfs"}):
            return False
        info = os.statvfs(path)
        return (os.access(path, os.W_OK | os.X_OK)
                and not info.f_flag & (os.ST_NOEXEC | os.ST_RDONLY)
                and info.f_bavail * info.f_frsize >= needed)
    except OSError:
        return False


def build_base(artifact_bytes: int, compiler_bytes: int, *, disk_only: bool = False) -> tuple[Path, bool, int]:
    try:
        _, available = memory_bytes()
    except (OSError, KeyError, ValueError):
        available = 0  # Unknown memory means disk and one job.
    if not disk_only and available >= artifact_bytes + compiler_bytes + RESERVE:
        for candidate in (Path("/tmp"), Path("/dev/shm")):
            if usable(candidate, artifact_bytes, ram=True):
                jobs = max(1, (available - artifact_bytes - RESERVE) // compiler_bytes)
                return candidate, True, min(os.process_cpu_count() or 1, jobs)
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    for candidate in (Path("/var/tmp"), cache):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        if usable(candidate, artifact_bytes + RESERVE, ram=False):
            jobs = 1 if disk_only else max(1, (available - RESERVE) // compiler_bytes)
            return candidate, False, min(os.process_cpu_count() or 1, jobs)
    raise OSError("No writable executable disk build directory with enough space (/var/tmp or user cache)")


class RetryOnDisk(Exception):
    """Unwind and remove RAM artifacts before the single disk retry."""


@dataclass(frozen=True)
class Workspace:
    path: Path
    ram: bool
    jobs: int

    @property
    def storage(self) -> str:
        return "tmpfs" if self.ram else "disk"

    def retry_on_disk(self, returncode: int, output: str) -> None:
        if not self.ram:
            return
        message = output.lower()
        resource_error = (
            returncode == -signal.SIGKILL
            or re.search(r"\bos error (?:12|28|122)\b", message)
            or any(marker in message for marker in (
                "no space left on device", "disk quota exceeded",
                "cannot allocate memory", "out of memory",
                "memory allocation of", "sigkill", "signal: 9", "signal 9",
            ))
        )
        if resource_error:
            raise RetryOnDisk


def scratch_root(base: Path) -> Path:
    return base / f"dusky-builds-{os.getuid()}"


def clean_stale_builds(cache: Path) -> None:
    """Recover scratch directories left by SIGKILL/power loss, under the build lock."""
    for base in (Path("/tmp"), Path("/dev/shm"), Path("/var/tmp"), cache):
        root = scratch_root(base)
        if root.is_dir():
            for path in root.iterdir():
                if path.is_dir():
                    shutil.rmtree(path)


def temporary_build(prefix: str, *, artifact_bytes: int = 3 * GIB,
                    compiler_bytes: int = 2 * GIB, max_jobs: int | None = None):
    """Serialize these installers, clean scratch storage, and retry RAM exhaustion once.

    Space/memory budgets are estimates, not compiler limits. Keep CARGO_HOME intact:
    it supplies the shared dependency sources needed for offline installation.
    """
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
            # This inode must remain in place: deleting it could split the lock.
            lock_path = Path("/tmp") / f"dusky-native-build-{os.getuid()}.lock"
            with lock_path.open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                with _BUILD_LOCK.set(lock.fileno()):
                    clean_stale_builds(cache)
                    for disk_only in (False, True):
                        base, ram, jobs = build_base(artifact_bytes, compiler_bytes, disk_only=disk_only)
                        if max_jobs is not None:
                            jobs = min(jobs, max_jobs)
                        root = scratch_root(base)
                        try:
                            root.mkdir(exist_ok=True)
                            with tempfile.TemporaryDirectory(prefix=prefix, dir=root) as temporary:
                                workspace = Workspace(Path(temporary), ram, jobs)
                                print(f"[INFO] Temporary build on {workspace.storage} at {workspace.path} "
                                      f"({jobs} jobs); artifacts removed afterward", flush=True)
                                return function(*args, **kwargs, workspace=workspace)
                        except RetryOnDisk:
                            if not ram:
                                raise
                        except OSError as error:
                            if not ram or error.errno not in {errno.ENOSPC, errno.ENOMEM, errno.EDQUOT}:
                                raise
                        finally:
                            try:
                                root.rmdir()
                            except OSError:
                                pass
                        print("[WARN] RAM build exhausted resources; retrying on disk with one job", flush=True)
        return wrapped
    return decorate
