#!/usr/bin/python3
"""Install the temporary CPython 3.15rc3 build on x86-64 Arch Linux.

Run with the distro Python 3.14+; /usr/bin/python* is never modified.
    python_rc3_install.py [check]
    python_rc3_install.py install [--reinstall] [--no-default] [--force] [--tarball PATH]
    python_rc3_install.py --undo [--purge]

Installation shadows python and python3 in /usr/local/bin. That directory
must precede /usr/bin in users' PATH for /usr/bin/env python3 shebangs.
After upgrading the distro Python to stable 3.15+, run --undo.
Undo removes recorded files and restores prior shadow symlinks; files added
later (including installed packages) are retained unless --purge is specified.
Recreate 3.14 virtual environments with 3.15; their interpreters and packages
are not migrated.
"""

import argparse
import contextlib
import errno
import fcntl
import hashlib
import json
import logging
import os
import platform
import pwd
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import NoReturn

VERSION = "3.15.0rc3"
ARCH_TAG = "x86_64-generic"
REPO = "dusklinux/dusky_python"
TAG = f"v{VERSION}"
BASE_URL = "https://github.com"
ASSET = f"dusky-python-{VERSION}-{ARCH_TAG}.tar.gz"
PREFIX = Path("/usr/local")
MARKER = PREFIX / "lib/dusky-python.json"
LOCKFILE = Path("/run/lock/dusky-python-install.lock")
SYSTEM_PYTHON = Path("/usr/bin/python3")
WANT_BIN = PREFIX / "bin/python3.15"
SHADOW_LINKS = {"python": "python3.15", "python3": "python3.15"}
OWNED_TREES = (Path("lib/python3.15"), Path("include/python3.15"))
OWNED_FILES = {
    "bin/python3.15", "bin/python3.15-config", "bin/idle3.15", "bin/pydoc3.15",
    "lib/libpython3.15.a", "lib/libpython3.15.so", "lib/libpython3.15.so.1.0",
    "lib/pkgconfig/python-3.15.pc", "lib/pkgconfig/python-3.15-embed.pc",
    "share/man/man1/python3.15.1",
}
SHARED_DIRS = {"bin", "lib", "include", "lib/pkgconfig", "share", "share/man", "share/man/man1"}
log = logging.getLogger("dusky-python")


def die(message: str) -> NoReturn:
    raise RuntimeError(message)


def exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def owned(rel: str) -> bool:
    path = Path(rel)
    return rel in OWNED_FILES or any(path == tree or tree in path.parents for tree in OWNED_TREES)


def managed_path(rel: str) -> Path:
    path = Path(rel)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        die(f"Invalid manifest path: {rel!r}")
    if not owned(rel) and rel not in SHARED_DIRS:
        die(f"Unexpected manifest path: {rel!r}")
    dest = PREFIX / path
    # Check the parent, not the final link: unlinking a link never follows it.
    if not dest.parent.resolve().is_relative_to(PREFIX.resolve()):
        die(f"Path parent escapes {PREFIX}: {dest}")
    return dest


def read_marker() -> dict | None:
    try:
        data = json.loads(MARKER.read_text())
    except FileNotFoundError:
        return None
    except ValueError as exc:
        die(f"Invalid installation marker {MARKER}: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        die(f"Invalid installation marker: {MARKER}")
    for rel in data["files"]:
        if not isinstance(rel, str):
            die("Manifest entries must be strings.")
        managed_path(rel)
    shadow = data.get("shadow", {})
    if not isinstance(shadow, dict):
        die("Invalid shadow metadata.")
    backups = shadow.get("backed_up", {})
    if not isinstance(backups, dict) or any(
        name not in SHADOW_LINKS or not isinstance(target, str)
        for name, target in backups.items()
    ):
        die("Invalid shadow backup metadata.")
    return data


def run_python(path: Path, code: str) -> str:
    result = subprocess.run(
        [str(path), "-I", "-B", "-c", code], capture_output=True, text=True,
        timeout=60, check=True,
    )
    return result.stdout.strip()


def probe_bin(path: Path = WANT_BIN) -> str | None:
    try:
        return run_python(path, "import platform; print(platform.python_version())")
    except (OSError, subprocess.SubprocessError):
        return None


def verify_runtime() -> None:
    if probe_bin(WANT_BIN) != VERSION:
        die(f"Installed interpreter does not report {VERSION}.")
    run_python(WANT_BIN,
               "import ssl, sqlite3, lzma, bz2, zlib, ctypes, readline, "
               "decimal, multiprocessing, compression.zstd; "
               "ssl.create_default_context(); "
               "sqlite3.connect(':memory:').execute('select 1').fetchone()")
    if probe_bin(SYSTEM_PYTHON) is None:
        die("Distro Python failed verification.")


def check_path() -> None:
    for name in SHADOW_LINKS:
        resolved = shutil.which(name)
        if resolved is None or Path(resolved).resolve() != WANT_BIN.resolve():
            log.warning("PATH %s resolves to %s; put /usr/local/bin first in users' PATH.",
                        name, resolved or "nothing")


@contextlib.contextmanager
def install_lock() -> Iterator[None]:
    LOCKFILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCKFILE.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            die("Another install/undo is running.")
        yield


class Transaction:
    """Keep replaced files on the same filesystem until verification succeeds.

    Roll back ordinary failures and Ctrl-C. This is not a crash journal:
    SIGKILL/power loss may require recovery from the logged backup directory.
    """

    def __init__(self, backup: Path):
        self.backup = backup
        self.touched: dict[Path, Path | None] = {}
        self.created_dirs: list[Path] = []

    def mkdir(self, path: Path) -> None:
        if path.is_dir():
            return
        self.mkdir(path.parent)
        path.mkdir()
        self.created_dirs.append(path)

    def save(self, path: Path) -> None:
        if path in self.touched:
            return
        if path.is_dir() and not path.is_symlink():
            die(f"Expected a file, found directory: {path}")
        saved = None
        if exists(path):
            saved = self.backup / path.relative_to(PREFIX)
            saved.parent.mkdir(parents=True, exist_ok=True)
            self.touched[path] = saved
            try:
                path.rename(saved)
            except BaseException:
                if not exists(saved):
                    del self.touched[path]
                raise
        else:
            self.touched[path] = None

    def link(self, path: Path, target: str) -> None:
        self.mkdir(path.parent)
        self.save(path)
        path.unlink(missing_ok=True)
        path.symlink_to(target)

    def rollback(self) -> None:
        for path, saved in reversed(self.touched.items()):
            path.unlink(missing_ok=True)
            if saved is not None:
                saved.rename(path)
        for path in reversed(self.created_dirs):
            path.rmdir()


@contextlib.contextmanager
def transaction() -> Iterator[Transaction]:
    backup = Path(tempfile.mkdtemp(prefix=".dusky-python-backup-", dir=PREFIX))
    tx = Transaction(backup)
    log.info("Rollback backup: %s", backup)
    try:
        yield tx
    except BaseException:
        try:
            tx.rollback()
        except BaseException:
            log.error("Rollback incomplete. Keep %s for manual recovery.", backup)
            raise
        shutil.rmtree(backup)
        raise
    else:
        shutil.rmtree(backup)


def write_marker(marker: dict, tx: Transaction) -> None:
    tx.mkdir(MARKER.parent)
    temporary = MARKER.with_suffix(".json.new")
    try:
        temporary.write_text(json.dumps(marker, indent=2) + "\n")
        tx.save(MARKER)
        temporary.replace(MARKER)
    finally:
        temporary.unlink(missing_ok=True)


def set_shadow(marker: dict, enabled: bool, tx: Transaction) -> None:
    backups = dict(marker.get("shadow", {}).get("backed_up", {}))
    for name, target in SHADOW_LINKS.items():
        link = PREFIX / "bin" / name
        ours = link.is_symlink() and os.readlink(link) == target
        if enabled:
            if ours:
                continue
            if exists(link):
                if not link.is_symlink():
                    die(f"Refusing to overwrite real file: {link}")
                backups[name] = os.readlink(link)
            tx.link(link, target)
        else:
            if ours or (not exists(link) and name in backups):
                tx.save(link)
                if name in backups:
                    tx.link(link, backups[name])
            backups.pop(name, None)
    marker["shadow"] = {"links": list(SHADOW_LINKS) if enabled else [], "backed_up": backups}


def download(url: str, dest: Path) -> None:
    log.info("Downloading %s", url)
    request = urllib.request.Request(url, headers={"User-Agent": "dusky-python-installer"})
    with urllib.request.urlopen(request, timeout=60) as response, dest.open("wb") as output:
        shutil.copyfileobj(response, output, length=1 << 20)
        output.flush()
        length = response.headers.get("Content-Length")
        if length is not None and dest.stat().st_size != int(length):
            die("Incomplete download.")


def sha256_of(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fetch_checksum(url: str) -> str:
    request = urllib.request.Request(url + ".sha256")
    with urllib.request.urlopen(request, timeout=60) as response:
        fields = response.read(4096).decode("ascii").split()
    if not fields:
        die("Checksum file is empty.")
    return fields[0]


def manifest(src: Path) -> list[str]:
    paths = [str(path.relative_to(src)) for path in src.rglob("*")]
    for rel in paths:
        managed_path(rel)
    return sorted(paths, key=lambda rel: (len(Path(rel).parts), rel), reverse=True)


def prune_dirs(paths: list[str]) -> None:
    for rel in sorted(set(paths), key=lambda rel: len(Path(rel).parts), reverse=True):
        path = managed_path(rel)
        if rel not in SHARED_DIRS and path.is_dir() and not path.is_symlink():
            try:
                path.rmdir()
            except OSError as exc:
                if exc.errno != errno.ENOTEMPTY:
                    raise


def cmd_install(args: argparse.Namespace) -> int:
    distro = platform.freedesktop_os_release()
    if distro.get("ID") != "arch" and "arch" not in distro.get("ID_LIKE", "").split():
        die("This build requires Arch Linux.")
    if os.uname().machine != "x86_64":
        die("This release asset supports x86-64 only.")
    info = run_python(SYSTEM_PYTHON,
                      "import sys; print(*sys.version_info[:2], sys.version_info.releaselevel)")
    major, minor, level = info.split()
    if (int(major), int(minor)) >= (3, 15) and level == "final":
        die("Distro Python is already stable 3.15+. Run --undo to use it.")
    marker = read_marker()
    if marker and marker.get("version") == VERSION and marker.get("arch") == ARCH_TAG and not args.reinstall:
        verify_runtime()
        with transaction() as tx:
            set_shadow(marker, not args.no_default, tx)
            write_marker(marker, tx)
        if not args.no_default:
            check_path()
        log.info("Already installed; default links updated.")
        return 0
    allow_force = bool(getattr(args, "reinstall", False) or getattr(args, "force", False))
    if marker is None and exists(WANT_BIN):
        if not allow_force:
            die(f"Unmanaged {WANT_BIN}; pass --force (or --reinstall) to replace, or move the existing build aside first.")
        log.warning("Unmanaged %s detected; replacing under --force.", WANT_BIN)

    # Stage on the destination filesystem, not /tmp (often a small tmpfs).
    with tempfile.TemporaryDirectory(prefix=".dusky-python-stage-", dir=PREFIX) as temporary:
        work = Path(temporary)
        tarball = work / ASSET
        local_src = None
        user = os.environ.get("SUDO_USER")
        try:
            user_home = Path(pwd.getpwnam(user).pw_dir) if user else Path.home()
        except (KeyError, ValueError):
            user_home = Path.home()

        if getattr(args, "tarball", None):
            local_src = Path(args.tarball).resolve()
            if not local_src.is_file():
                die(f"Specified tarball not found: {args.tarball}")
        elif (Path.cwd() / ASSET).is_file():
            local_src = Path.cwd() / ASSET
        elif (user_home / ".cache/dusky-python-build" / ASSET).is_file():
            local_src = user_home / ".cache/dusky-python-build" / ASSET
        elif (Path.home() / ".cache/dusky-python-build" / ASSET).is_file():
            local_src = Path.home() / ".cache/dusky-python-build" / ASSET

        if local_src is not None:
            log.info("Using local archive: %s", local_src)
            shutil.copy2(local_src, tarball)
            expected = args.checksum or ""
            if not expected:
                sha_file = local_src.with_suffix(local_src.suffix + ".sha256")
                if not sha_file.is_file():
                    sha_file = local_src.with_name(local_src.name + ".sha256")
                if sha_file.is_file():
                    fields = sha_file.read_text().split()
                    if fields:
                        expected = fields[0]
            actual = sha256_of(tarball)
            if expected and actual.lower() != expected.lower():
                die(f"Checksum mismatch: expected {expected}, got {actual}")
        else:
            url = f"{args.base_url.rstrip('/')}/{args.repo}/releases/download/{args.tag}/{ASSET}"
            expected = args.checksum or fetch_checksum(url)
            if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
                die("Expected SHA-256 must contain exactly 64 hexadecimal digits.")
            download(url, tarball)
            actual = sha256_of(tarball)
            if actual.lower() != expected.lower():
                die(f"Checksum mismatch: expected {expected}, got {actual}")
        stage = work / "stage"
        with tarfile.open(tarball, "r:gz") as archive:
            expanded = sum(member.size for member in archive if member.isfile())
            # Extraction plus destination copy; prior files are renamed, not copied.
            needed = 2 * expanded + 64 * 1024 * 1024
            if shutil.disk_usage(PREFIX).free < needed:
                die(f"Need {needed // (1024 * 1024)} MiB free under {PREFIX}.")
            archive.extractall(stage, filter="data")
        src = stage / "usr/local"
        if not (src / "bin/python3.15").is_file():
            die("Archive is missing usr/local/bin/python3.15.")
        files = manifest(src)
        old_files = set(marker["files"] if marker else [])
        for rel in files:
            dest = managed_path(rel)
            source = src / rel
            if source.is_dir() and not source.is_symlink():
                if exists(dest) and (not dest.is_dir() or (dest.is_symlink() and rel not in SHARED_DIRS)):
                    die(f"Directory conflict: {dest}")
            elif exists(dest) and rel not in old_files:
                if not allow_force:
                    die(f"Refusing to overwrite unmanaged file: {dest}. Pass --force to replace.")
        new_marker = {
            "version": VERSION, "arch": ARCH_TAG, "repo": args.repo,
            "tag": args.tag, "asset": ASSET, "asset_sha256": actual,
            "files": files, "shadow": (marker or {}).get("shadow", {}),
        }
        with transaction() as tx:
            for rel in old_files:
                if rel in SHARED_DIRS:
                    continue
                dest = managed_path(rel)
                if exists(dest) and (not dest.is_dir() or dest.is_symlink()):
                    tx.save(dest)
            for rel in reversed(files):
                source, dest = src / rel, managed_path(rel)
                if source.is_dir() and not source.is_symlink():
                    tx.mkdir(dest)
                else:
                    tx.mkdir(dest.parent)
                    tx.save(dest)
                    if source.is_symlink():
                        dest.symlink_to(os.readlink(source))
                    else:
                        shutil.copy2(source, dest)
            verify_runtime()
            set_shadow(new_marker, not args.no_default, tx)
            write_marker(new_marker, tx)
        prune_dirs(list(old_files - set(files)))
    if not args.no_default:
        check_path()
    log.info("Installed %s. Distro Python untouched.", VERSION)
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    if probe_bin(SYSTEM_PYTHON) is None:
        die("Distro Python is missing or broken; cannot undo.")
    marker = read_marker()
    allow_force = bool(getattr(args, "force", False))
    purge = bool(getattr(args, "purge", False) or allow_force)
    if marker is None:
        if exists(WANT_BIN):
            if not allow_force:
                die("No installation marker; refusing to guess which files belong to this build. Pass --force to clean known trees.")
            with transaction() as tx:
                for name, target in SHADOW_LINKS.items():
                    link = PREFIX / "bin" / name
                    if link.is_symlink() and os.readlink(link) == target:
                        tx.save(link)
                for rel in OWNED_FILES:
                    path = PREFIX / rel
                    if exists(path) and (path.is_symlink() or not path.is_dir()):
                        tx.save(path)
                for tree in OWNED_TREES:
                    path = PREFIX / tree
                    if path.is_dir() and not path.is_symlink():
                        shutil.rmtree(path, ignore_errors=True)
            log.info("Force undo complete. Distro Python untouched.")
            return 0
        log.info("No managed installation. Nothing to undo.")
        return 0
    with transaction() as tx:
        set_shadow(marker, False, tx)
        for rel in marker["files"]:
            if rel in SHARED_DIRS:
                continue
            path = managed_path(rel)
            if exists(path) and (path.is_symlink() or not path.is_dir()):
                tx.save(path)
        tx.save(MARKER)
        if probe_bin(SYSTEM_PYTHON) is None:
            die("Distro Python failed verification during undo.")
    prune_dirs(marker["files"])
    for tree in OWNED_TREES:
        t_path = PREFIX / tree
        if t_path.exists():
            if purge:
                shutil.rmtree(t_path, ignore_errors=True)
                log.info("Purged %s.", t_path)
            else:
                log.info("Retained unrecorded files under %s (pass --purge to remove).", t_path)
    log.info("Undo complete. Distro Python untouched.")
    return 0


def cmd_check(_args: argparse.Namespace) -> int:
    marker = read_marker()
    print(f"system_python: {probe_bin(SYSTEM_PYTHON) or 'unavailable'} ({SYSTEM_PYTHON})")
    version = probe_bin(WANT_BIN)
    print(f"python3.15: {version or 'unavailable'}")
    print(f"marker: {'present' if marker is not None else 'absent'}")
    print(f"wanted: {VERSION} ({ARCH_TAG})")
    print(f"installed: {bool(marker and marker.get('version') == VERSION and marker.get('arch') == ARCH_TAG and version == VERSION)}")
    for name in SHADOW_LINKS:
        path = shutil.which(name)
        print(f"PATH {name}: {path or 'not found'} ({probe_bin(Path(path)) if path else 'unavailable'})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    # Share flags so they work both before and after the command.
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS,
                                     allow_abbrev=False)
    common.add_argument("--repo")
    common.add_argument("--tag")
    common.add_argument("--base-url", help="Root serving the GitHub-style release path.")
    common.add_argument("--checksum", help="Expected SHA-256; otherwise fetch asset.sha256.")
    common.add_argument("-v", "--verbose", action="store_true")
    common.add_argument("--undo", action="store_true", help="Same as uninstall.")
    common.add_argument("--force", action="store_true", help="Force action on unmanaged files.")
    common.add_argument("--purge", action="store_true", help="Purge all files under owned trees on undo.")
    common.add_argument("--tarball", help="Use local archive instead of downloading.")
    # Parent arguments must be attached before creating subparsers.
    parser = argparse.ArgumentParser(description=__doc__, parents=[common], allow_abbrev=False,
                                    formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("check", parents=[common], allow_abbrev=False)
    install = sub.add_parser("install", parents=[common], allow_abbrev=False)
    install.add_argument("--reinstall", action="store_true")
    install.add_argument("--no-default", action="store_true")
    sub.add_parser("uninstall", aliases=["undo"], parents=[common], allow_abbrev=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    defaults = {
        "repo": REPO, "tag": TAG, "base_url": BASE_URL, "checksum": "",
        "verbose": False, "undo": False, "force": False, "purge": False,
        "tarball": None, "reinstall": False, "no_default": False,
    }
    for name, value in defaults.items():
        if not hasattr(args, name):
            setattr(args, name, value)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s: %(message)s")
    action = "uninstall" if args.undo or args.cmd in ("undo", "uninstall") else args.cmd
    try:
        if action not in ("install", "uninstall"):
            return cmd_check(args)
        # Always mutate under distro Python, including when invoked from a venv.
        if Path(sys.executable).resolve() != SYSTEM_PYTHON.resolve() or sys.prefix != sys.base_prefix:
            os.execv(str(SYSTEM_PYTHON), [str(SYSTEM_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])
        if os.geteuid() != 0:
            os.execvp("sudo", ["sudo", str(SYSTEM_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])
        PREFIX.mkdir(parents=True, exist_ok=True)
        with install_lock():
            return cmd_install(args) if action == "install" else cmd_uninstall(args)
    except (OSError, ValueError, RuntimeError, tarfile.TarError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stderr", None)
        log.error("%s%s", exc, f"\n{detail.strip()}" if detail else "")
        return 1
    except KeyboardInterrupt:
        log.error("Interrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
