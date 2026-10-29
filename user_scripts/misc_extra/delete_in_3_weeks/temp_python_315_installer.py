#!/usr/bin/python3
"""Install the temporary CPython 3.15 runtime on x86-64 Arch Linux.

Run with the distro Python 3.14+; /usr/bin/python* is never modified.
    temp_python_315_installer.py [check]
    temp_python_315_installer.py install [--reinstall] [--no-default] [--force] [--tarball PATH]
    temp_python_315_installer.py --undo [--purge]

By default, installation shadows python and python3 in /usr/local/bin. That directory
must precede /usr/bin in users' PATH for /usr/bin/env python3 shebangs.
--no-default restores recorded prior links and leaves unrelated links alone.
--reinstall replaces the recorded payload; --force also permits replacement of
unrecorded payload files. On undo, --force removes known trees and implies --purge.
After upgrading the distro Python to stable 3.15+, run --undo.
Undo removes recorded files and restores prior shadow symlinks; files added
later (including installed packages) are retained unless --purge is specified.
Recreate 3.14 virtual environments with 3.15; their interpreters and packages
are not migrated.

The archive must contain a complete /usr/local runtime, including Python 3.15
builds of PyGObject and pycairo. Installation never invokes pip or downloads
dependencies. GTK 4 and the native libraries required by the build must already
be installed. Absolute /usr/bin/python3 launchers keep using the distro Python.
An already selected, unmanaged compatible Python 3.15 or newer is checked and
retained on a normal install. Managed older builds are upgraded.
Keep this bootstrap script parseable by Python 3.14 so install and undo work
before the distro upgrade; Python 3.15-only syntax belongs in the payload.
"""

import argparse
import contextlib
import errno
import fcntl
import hashlib
import http.client
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
import zlib
from collections.abc import Iterator
from pathlib import Path
from typing import NoReturn

VERSION = "3.15.2"
ARCH_TAG = "x86_64-generic"
REPO = "dusklinux/dusky_python"
TAG = f"v{VERSION}"
BASE_URL = "https://github.com"
ASSET = f"dusky-python-{VERSION}-{ARCH_TAG}-desktop.tar.gz"
PREFIX = Path("/usr/local")
MARKER = PREFIX / "lib/dusky-python.json"
LOCKFILE = Path("/run/lock/dusky-python-install.lock")
SYSTEM_PYTHON = Path("/usr/bin/python3")
PY_XY = ".".join(VERSION.split(".")[:2])
WANT_BIN = PREFIX / f"bin/python{PY_XY}"
SHADOW_LINKS = {"python": f"python{PY_XY}", "python3": f"python{PY_XY}"}
OWNED_TREES = (Path(f"lib/python{PY_XY}"), Path(f"include/python{PY_XY}"))
OWNED_FILES = {
    f"bin/python{PY_XY}", f"bin/python{PY_XY}-config", f"bin/idle{PY_XY}", f"bin/pydoc{PY_XY}",
    f"lib/libpython{PY_XY}.a", f"lib/libpython{PY_XY}.so", f"lib/libpython{PY_XY}.so.1.0",
    f"lib/pkgconfig/python-{PY_XY}.pc", f"lib/pkgconfig/python-{PY_XY}-embed.pc",
    f"share/man/man1/python{PY_XY}.1",
}
SHARED_DIRS = {"bin", "lib", "include", "lib/pkgconfig", "share", "share/man", "share/man/man1"}
STDLIB_PROBE = """\
import ssl, sqlite3, lzma, bz2, zlib, ctypes, readline
import decimal, multiprocessing, compression.zstd
from contextlib import closing

ssl.create_default_context()
with closing(sqlite3.connect(':memory:')) as db:
    assert db.execute('select 1').fetchone() == (1,)
"""
DESKTOP_PROBE = (
    "import gi, cairo; gi.require_version('Gtk', '4.0'); "
    "gi.require_foreign('cairo'); from gi.repository import Gtk; "
    "assert Gtk.get_major_version() == 4; "
    "cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)); "
    "print(gi.__version__)"
)
UPDATER_PROBE = (
    "import rich, textual; from importlib.metadata import version; "
    "v = tuple(map(int, version('textual').split('.')[:3])); "
    "assert v >= (8, 2, 8), 'Textual 8.2.8 or newer is required'"
)
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
    if path.is_absolute() or not path.parts or ".." in path.parts or str(path) != rel:
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
        data = json.loads(MARKER.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError as exc:
        die(f"Invalid installation marker {MARKER}: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        die(f"Invalid installation marker: {MARKER}")
    version = data.get("version")
    if (not isinstance(version, str) or not re.fullmatch(r"3\.15\.\d+(?:rc\d+)?", version)
            or data.get("arch") != ARCH_TAG):
        die(f"Installation marker belongs to a different build: {MARKER}")
    for rel in data["files"]:
        if not isinstance(rel, str):
            die("Manifest entries must be strings.")
        managed_path(rel)
    shadow = data.get("shadow", {})
    if not isinstance(shadow, dict):
        die("Invalid shadow metadata.")
    links = shadow.get("links", [])
    if not isinstance(links, list) or any(
        not isinstance(name, str) or name not in SHADOW_LINKS for name in links
    ):
        die("Invalid shadow link metadata.")
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
        encoding="utf-8", errors="replace", timeout=60, check=True,
    )
    if result.stderr:
        log.debug("%s: %s", path, result.stderr.rstrip())
    return result.stdout.strip()


def probe_bin(path: Path = WANT_BIN) -> str | None:
    try:
        return run_python(path, "import platform; print(platform.python_version())")
    except (OSError, subprocess.SubprocessError):
        return None


def verify_runtime(path: Path) -> None:
    """Verify the staged or installed runtime without changing its packages."""
    if probe_bin(path) != VERSION:
        die(f"{path} does not report {VERSION}.")
    prefix = run_python(path, "import sys; print(sys.prefix)")
    if Path(prefix).resolve() != path.parent.parent.resolve():
        die(f"{path} loaded another installation's standard library: {prefix}")
    run_python(path, STDLIB_PROBE)
    run_python(path, DESKTOP_PROBE)
    run_python(path, UPDATER_PROBE)
    run_python(path, "import pyudev; from PIL import Image")


def check_path() -> None:
    for name in SHADOW_LINKS:
        resolved = shutil.which(name)
        if resolved is None or Path(resolved).resolve() != WANT_BIN.resolve():
            log.warning("PATH %s resolves to %s; put /usr/local/bin first in users' PATH.",
                        name, resolved or "nothing")


@contextlib.contextmanager
def install_lock() -> Iterator[None]:
    LOCKFILE.parent.mkdir(parents=True, exist_ok=True)
    # Closing releases flock; keep the pathname so concurrent opens share it.
    with LOCKFILE.open("ab") as lock:
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
        errors = []
        for path, saved in reversed(self.touched.items()):
            try:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
                if saved is not None:
                    saved.rename(path)
            except OSError as exc:
                errors.append(f"{path}: {exc}")
        for path in reversed(self.created_dirs):
            try:
                path.rmdir()
            except FileNotFoundError:
                pass
            except OSError as exc:
                errors.append(f"{path}: {exc}")
        if errors:
            die("Rollback errors:\n" + "\n".join(errors))


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
        temporary.write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
        tx.save(MARKER)
        temporary.replace(MARKER)
    finally:
        temporary.unlink(missing_ok=True)


def set_shadow(marker: dict, enabled: bool, tx: Transaction) -> None:
    shadow = marker.get("shadow", {})
    recorded = shadow.get("links", [])
    backups = dict(shadow.get("backed_up", {}))
    for name, target in SHADOW_LINKS.items():
        link = PREFIX / "bin" / name
        ours = link.is_symlink() and os.readlink(link) == target
        if enabled:
            if ours and name in recorded:
                continue
            if exists(link):
                if not link.is_symlink():
                    die(f"Refusing to overwrite real file: {link}")
                backups[name] = os.readlink(link)
            if not ours:
                tx.link(link, target)
        else:
            if name not in recorded and name not in backups:
                continue
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


def extract_archive(tarball: Path, stage: Path) -> Path:
    """Reject layout mistakes before touching the destination installation."""
    with tarfile.open(tarball, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts:
                die(f"Invalid archive path: {member.name!r}")
            if path in (Path("usr"), Path("usr/local")):
                if not member.isdir():
                    die(f"Expected archive directory: {member.name}")
                continue
            if not path.is_relative_to("usr/local"):
                die(f"Unexpected archive path: {member.name}")
            rel = str(path.relative_to("usr/local"))
            managed_path(rel)
            if rel in SHARED_DIRS or Path(rel) in OWNED_TREES:
                if not member.isdir():
                    die(f"Expected archive directory: {member.name}")
            elif not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
                die(f"Unsupported archive member: {member.name}")
        expanded = sum(member.size for member in members if member.isfile() or member.islnk())
        # Files are renamed from staging into place on the same filesystem.
        needed = expanded + 64 * 1024 * 1024
        if shutil.disk_usage(PREFIX).free < needed:
            die(f"Need {needed // (1024 * 1024)} MiB free under {PREFIX}.")
        archive.extractall(stage, members=members, filter="data")
    src = stage / "usr/local"
    if not (src / f"bin/python{PY_XY}").is_file():
        die(f"Archive is missing usr/local/bin/python{PY_XY}.")
    return src


def prune_dirs(paths: list[str]) -> None:
    for rel in sorted(set(paths), key=lambda rel: len(Path(rel).parts), reverse=True):
        path = managed_path(rel)
        if rel not in SHARED_DIRS and path.is_dir() and not path.is_symlink():
            try:
                path.rmdir()
            except OSError as exc:
                if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                    log.warning("Could not prune %s: %s", path, exc)


def cmd_install(args: argparse.Namespace) -> int:
    distro = platform.freedesktop_os_release()
    if distro.get("ID") != "arch" and "arch" not in distro.get("ID_LIKE", "").split():
        die("This build requires Arch Linux.")
    if os.uname().machine != "x86_64":
        die("This release asset supports x86-64 only.")
    marker = read_marker()
    if marker and tuple(map(int, re.match(r"(\d+)\.(\d+)\.(\d+)", marker["version"]).groups())) > tuple(map(int, VERSION.split('.'))):
        die(f"Managed Python {marker['version']} is newer than {VERSION}; update this installer instead of downgrading.")
    if marker is None and not (args.reinstall or args.force or args.no_default or args.tarball):
        selected = shutil.which("python3")
        if selected:
            info = run_python(Path(selected),
                              "import sys; print(*sys.version_info[:3], sys.version_info.releaselevel, sys.version_info.serial)")
            major, minor, micro, level, serial = info.split()
            parts = (int(major), int(minor), int(micro))
            compatible = parts > (3, 15, 0) or (
                parts == (3, 15, 0) and
                (level == "final" or (level == "candidate" and int(serial) >= 3))
            )
            if compatible:
                run_python(Path(selected), STDLIB_PROBE)
                run_python(Path(selected), DESKTOP_PROBE)
                run_python(Path(selected), UPDATER_PROBE)
                log.info("Selected Python %s is already compatible; leaving it unchanged.",
                         ".".join(map(str, parts)))
                return 0
    info = run_python(SYSTEM_PYTHON,
                      "import sys; print(*sys.version_info[:2], sys.version_info.releaselevel)")
    major, minor, level = info.split()
    if (int(major), int(minor)) >= (3, 15) and level == "final":
        if marker:
            log.warning("Distro Python is already stable 3.15+. Run --undo after the updater exits to use it.")
        else:
            log.info("Distro Python is already stable 3.15+; no temporary installation is needed.")
        return 0
    if marker and marker.get("version") == VERSION and not args.reinstall:
        verify_runtime(WANT_BIN)
        with transaction() as tx:
            set_shadow(marker, not args.no_default, tx)
            write_marker(marker, tx)
        if not args.no_default:
            check_path()
        log.info("Already installed; default links updated.")
        log.info("Note: Restart your user session or reboot if running services need to pick up changes.")
        return 0
    allow_force = args.force
    if marker is None and exists(WANT_BIN):
        if not allow_force:
            die(f"Unmanaged {WANT_BIN}; pass --force to replace, or move the existing build aside first.")
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
                sha_file = local_src.with_name(local_src.name + ".sha256")
                if sha_file.is_file():
                    fields = sha_file.read_text(encoding="ascii").split()
                    if fields:
                        expected = fields[0]
        else:
            url = f"{args.base_url.rstrip('/')}/{args.repo}/releases/download/{args.tag}/{ASSET}"
            expected = args.checksum or fetch_checksum(url)
        if expected and not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            die("Expected SHA-256 must contain exactly 64 hexadecimal digits.")
        if local_src is None:
            download(url, tarball)
        actual = sha256_of(tarball)
        if expected:
            if actual.lower() != expected.lower():
                die(f"Checksum mismatch: expected {expected}, got {actual}")
        else:
            log.warning("Local archive has no checksum; SHA-256: %s", actual)
        src = extract_archive(tarball, work / "stage")
        verify_runtime(src / f"bin/python{PY_XY}")
        files = manifest(src)
        old_files = set(marker["files"] if marker else [])
        for rel in files:
            dest = managed_path(rel)
            source = src / rel
            if source.is_dir() and not source.is_symlink():
                if exists(dest) and (not dest.is_dir() or (dest.is_symlink() and rel not in SHARED_DIRS)):
                    die(f"Directory conflict: {dest}")
            else:
                if dest.is_dir() and not dest.is_symlink():
                    die(f"File conflicts with directory: {dest}")
                if exists(dest) and rel not in old_files and not allow_force:
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
                    source.rename(dest)
            verify_runtime(WANT_BIN)
            if probe_bin(SYSTEM_PYTHON) is None:
                die("Distro Python failed verification.")
            set_shadow(new_marker, not args.no_default, tx)
            write_marker(new_marker, tx)
        prune_dirs(list(old_files - set(files)))
    if not args.no_default:
        check_path()
    log.info("Installed %s. Distro Python untouched.", VERSION)
    log.info("Note: Restart your user session or reboot so running services and apps use the new Python runtime.")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    if probe_bin(SYSTEM_PYTHON) is None:
        die("Distro Python is missing or broken; cannot undo.")
    marker = read_marker()
    purge = args.purge or args.force
    if marker is None:
        leftovers = any(exists(PREFIX / rel) for rel in (*OWNED_FILES, *OWNED_TREES))
        leftovers |= any(
            (PREFIX / "bin" / name).is_symlink()
            and os.readlink(PREFIX / "bin" / name) == target
            for name, target in SHADOW_LINKS.items()
        )
        if not leftovers:
            log.info("No managed installation. Nothing to undo.")
            return 0
        if not args.force:
            if exists(WANT_BIN):
                die("No installation marker; pass --force to remove known files and trees.")
            log.info("No managed installation. Unrecorded remnants retained (use --force to remove known trees).")
            return 0
    files = marker["files"] if marker else sorted(OWNED_FILES)
    with transaction() as tx:
        if marker:
            set_shadow(marker, False, tx)
        else:
            for name, target in SHADOW_LINKS.items():
                link = PREFIX / "bin" / name
                if link.is_symlink() and os.readlink(link) == target:
                    tx.save(link)
        if purge:
            # Save whole trees first; never save their individual children too.
            for tree in OWNED_TREES:
                path = managed_path(str(tree))
                if exists(path):
                    tx.save(path)
        for rel in files:
            if rel in SHARED_DIRS or (purge and any(
                Path(rel) == tree or tree in Path(rel).parents for tree in OWNED_TREES
            )):
                continue
            path = managed_path(rel)
            if exists(path) and (path.is_symlink() or not path.is_dir()):
                tx.save(path)
        tx.save(MARKER)
        if probe_bin(SYSTEM_PYTHON) is None:
            die("Distro Python failed verification during undo.")
    if not purge:
        prune_dirs(files)
        for tree in OWNED_TREES:
            if exists(PREFIX / tree):
                log.info("Retained unrecorded files under %s (pass --purge to remove).", PREFIX / tree)
    log.info("Undo complete. Distro Python untouched.")
    log.info("Note: Restart your user session or reboot so running services pick up changes.")
    return 0


def cmd_check(_args: argparse.Namespace) -> int:
    marker = read_marker()
    system_version = probe_bin(SYSTEM_PYTHON)
    print(f"system_python: {system_version or 'unavailable'} ({SYSTEM_PYTHON})")
    version = probe_bin(WANT_BIN)
    print(f"python{PY_XY}: {version or 'unavailable'}")
    print(f"marker: {'present' if marker is not None else 'absent'}")
    print(f"wanted: {VERSION} ({ARCH_TAG})")
    print(f"installed: {bool(marker and marker.get('version') == VERSION and marker.get('arch') == ARCH_TAG and version == VERSION)}")
    for name in SHADOW_LINKS:
        path = shutil.which(name)
        print(f"PATH {name}: {path or 'not found'} ({probe_bin(Path(path)) if path else 'unavailable'})")
    target_bin = WANT_BIN if exists(WANT_BIN) else SYSTEM_PYTHON
    try:
        gi_ver = run_python(target_bin, DESKTOP_PROBE)
        gi_status = f"available (PyGObject {gi_ver})"
    except (OSError, ValueError, subprocess.SubprocessError):
        gi_status = "missing"
    print(f"desktop_gi: {gi_status}")
    if marker is not None or exists(WANT_BIN):
        verify_runtime(WANT_BIN)
        print("runtime: verified")
    if marker:
        for name in marker.get("shadow", {}).get("links", []):
            link = PREFIX / "bin" / name
            if not link.is_symlink() or os.readlink(link) != SHADOW_LINKS[name]:
                die(f"Managed default link changed or missing: {link}")
    return 0 if system_version is not None else 1


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
    common.add_argument("--force", action="store_true",
                        help="Replace unmanaged files on install; remove known trees on undo (implies --purge).")
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
    cli_args = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    args = parser.parse_args(cli_args)
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
    if args.undo and args.cmd in ("install", "check"):
        parser.error("--undo cannot be combined with install or check")
    if args.purge and action != "uninstall":
        parser.error("--purge requires uninstall or --undo")
    try:
        if action not in ("install", "uninstall"):
            return cmd_check(args)
        # Always mutate under distro Python, including when invoked from a venv.
        command = [str(SYSTEM_PYTHON), "-I", "-B", str(Path(__file__).resolve()), *cli_args]
        if (Path(sys.executable).resolve() != SYSTEM_PYTHON.resolve()
                or sys.prefix != sys.base_prefix or not sys.flags.isolated):
            os.execv(str(SYSTEM_PYTHON), command)
        if os.geteuid() != 0:
            os.execv("/usr/bin/sudo", ["sudo", *command])
        # System-wide directories must remain traversable for ordinary users.
        os.umask(0o022)
        PREFIX.mkdir(parents=True, exist_ok=True)
        with install_lock():
            return cmd_install(args) if action == "install" else cmd_uninstall(args)
    except (OSError, ValueError, RuntimeError, EOFError, zlib.error,
            http.client.HTTPException, tarfile.TarError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stderr", None)
        log.error("%s%s", exc, f"\n{detail.strip()}" if detail else "")
        return 1
    except KeyboardInterrupt:
        log.error("Interrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
