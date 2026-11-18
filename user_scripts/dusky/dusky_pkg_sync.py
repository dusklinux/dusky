#!/usr/bin/env python3
"""
dusky_pkg_sync: Atomic precompiled package updater for Dusky Arch Linux.

Detects both version/release bumps (via Arch `vercmp`) and same-version
source rebuilds (via `source.sha256` and local pacman `%BUILDDATE%`), verifies
SHA-256 digests in tmpfs, and installs updates atomically via `pacman -U`.
"""

import argparse
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import secrets
import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
import time
from typing import Any, TextIO
import urllib.error
import urllib.parse
import urllib.request

if sys.version_info < (3, 15):
    sys.stderr.write(
        f"dusky-pkg-sync requires Python 3.15+, found {sys.version_info.major}.{sys.version_info.minor}\n"
    )
    sys.exit(2)

DEFAULT_REPO_URL = "https://raw.githubusercontent.com/dusklinux/dusky_packages/main"
PACMAN_DB_LOCK = Path("/var/lib/pacman/db.lck")
PACMAN_LOCAL_DB = Path("/var/lib/pacman/local")
USER_AGENT = "dusky-pkg-sync/2.1 (Dusky Arch Linux; Python 3.15)"
CHUNK_SIZE = 65_536  # 64 KiB streaming buffer
MAX_RETRIES = 3

PKGNAME_RE = re.compile(r"^[a-z0-9@._+-]+$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
SAFE_PKG_BASENAME_RE = re.compile(r"^[a-zA-Z0-9@._+-]+\.pkg\.tar\.zst$")


class DuskySyncError(Exception):
    """Base exception for dusky-pkg-sync failures."""


class TerminatedError(BaseException):
    """Raised when the process receives SIGTERM so cleanup handlers execute."""


class ManifestError(DuskySyncError):
    """Raised when remote manifest retrieval or schema validation fails."""


class ChecksumMismatchError(DuskySyncError):
    """Raised when a downloaded package fails SHA-256 or byte-size validation."""


class PacmanError(DuskySyncError):
    """Raised when local pacman inspection or transaction execution fails."""


class PacmanLockError(PacmanError):
    """Raised when /var/lib/pacman/db.lck remains locked past the wait timeout."""


class PackageAction(StrEnum):
    INSTALL = "install"
    UPGRADE = "upgrade"
    REBUILD_UPGRADE = "rebuild_upgrade"
    REINSTALL = "reinstall"
    UP_TO_DATE = "up_to_date"
    DOWNGRADE_SKIPPED = "downgrade_skipped"


@dataclass(frozen=True, slots=True)
class LocalPackageState:
    pkgname: str
    pkgver: str
    build_date: int | None
    source_sha256: str | None


@dataclass(frozen=True, slots=True)
class RemotePackage:
    pkgname: str
    pkgver: str
    pkgdesc: str
    arch: str
    filename: str
    sha256: str
    size_bytes: int
    installed_size: int
    build_date: int
    source_sha256: str | None = None

    @classmethod
    def from_dict(cls, key_name: str, data: dict[str, Any]) -> RemotePackage:
        try:
            pkgname = str(data["pkgname"])
            if pkgname != key_name or not PKGNAME_RE.fullmatch(pkgname):
                raise ManifestError(f"Invalid or mismatched pkgname in manifest: {pkgname!r}")

            pkgver = str(data["pkgver"]).strip()
            if not pkgver:
                raise ManifestError(f"Empty pkgver for {pkgname}")

            arch = str(data.get("arch", "x86_64")).strip()
            filename = str(data["filename"]).strip()
            posix_rel = PurePosixPath(filename)
            if (
                posix_rel.is_absolute()
                or ".." in posix_rel.parts
                or not SAFE_PKG_BASENAME_RE.fullmatch(posix_rel.name)
            ):
                raise ManifestError(f"Unsafe package path in manifest for {pkgname}: {filename!r}")

            sha256 = str(data["sha256"]).strip()
            if not SHA256_RE.fullmatch(sha256):
                raise ManifestError(f"Invalid SHA-256 digest in manifest for {pkgname}: {sha256!r}")

            size_bytes = int(data["size_bytes"])
            if size_bytes <= 0:
                raise ManifestError(f"Invalid size_bytes for {pkgname}: {size_bytes}")

            src_raw = data.get("source_sha256")
            source_sha256 = str(src_raw).strip().lower() if src_raw is not None else None

            return cls(
                pkgname=pkgname,
                pkgver=pkgver,
                pkgdesc=str(data.get("pkgdesc", "")),
                arch=arch,
                filename=posix_rel.as_posix(),
                sha256=sha256.lower(),
                size_bytes=size_bytes,
                installed_size=int(data.get("installed_size", 0)),
                build_date=int(data.get("build_date", 0)),
                source_sha256=source_sha256,
            )
        except ManifestError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ManifestError(f"Malformed package entry for {key_name!r}: {exc}") from exc


@dataclass(frozen=True, slots=True)
class PackagePlan:
    pkgname: str
    action: PackageAction
    reason: str
    local_ver: str | None
    remote_ver: str
    arch: str
    size_bytes: int
    filename: str
    sha256: str

    @property
    def needs_download(self) -> bool:
        return self.action in {
            PackageAction.INSTALL,
            PackageAction.UPGRADE,
            PackageAction.REBUILD_UPGRADE,
            PackageAction.REINSTALL,
        }


type PackageCatalog = dict[str, RemotePackage]


class ConsoleUI:
    """Coordinates status, verbose logs, and rich progress bars strictly on stderr."""

    def __init__(self, *, json_mode: bool, verbose: bool) -> None:
        self.json_mode = json_mode
        self.verbose = verbose
        self.is_tty = sys.stderr.isatty() and not json_mode
        self._rich_console: Any = None

    def attach_rich_console(self, console: Any) -> None:
        self._rich_console = console

    def detach_rich_console(self) -> None:
        self._rich_console = None

    def info(self, msg: str) -> None:
        if self.json_mode:
            return
        if self._rich_console is not None:
            self._rich_console.print(msg, highlight=False)
        else:
            print(msg, file=sys.stderr)

    def debug(self, msg: str) -> None:
        if self.json_mode or not self.verbose:
            return
        if self._rich_console is not None:
            self._rich_console.print(f"[dim][debug] {msg}[/dim]", highlight=False)
        else:
            print(f"\033[2m[debug] {msg}\033[0m", file=sys.stderr)

    def warn(self, msg: str) -> None:
        if self.json_mode:
            return
        if self._rich_console is not None:
            self._rich_console.print(f"[bold yellow][warn][/bold yellow] {msg}", highlight=False)
        else:
            print(f"\033[33m[warn]\033[0m {msg}", file=sys.stderr)

    def error(self, msg: str) -> None:
        if self.json_mode:
            return
        if self._rich_console is not None:
            self._rich_console.print(f"[bold red][error][/bold red] {msg}", highlight=False)
        else:
            print(f"\033[31m[error]\033[0m {msg}", file=sys.stderr)


def build_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def preflight_checks() -> str:
    """Verify required Arch Linux binaries exist and return host architecture."""
    for binary in ("pacman", "vercmp"):
        if shutil.which(binary) is None:
            raise PacmanError(f"Required system binary '{binary}' was not found in PATH.")
    return platform.machine()


def _inspect_lock_holder(lock_path: Path) -> tuple[int | None, bool]:
    """Read optional PID from pacman db.lck and check whether that process is alive."""
    try:
        raw = lock_path.read_text(encoding="utf-8", errors="ignore").strip()
        if raw.isdigit():
            pid = int(raw)
            return pid, Path(f"/proc/{pid}").exists()
    except OSError:
        pass
    return None, False


def check_pacman_lock(wait_seconds: int, ui: ConsoleUI) -> None:
    """Wait up to `wait_seconds` for /var/lib/pacman/db.lck to clear, or raise PacmanLockError."""
    if not PACMAN_DB_LOCK.exists():
        return

    deadline = time.monotonic() + max(0, wait_seconds)
    notified = False

    while PACMAN_DB_LOCK.exists():
        pid, alive = _inspect_lock_holder(PACMAN_DB_LOCK)
        if time.monotonic() >= deadline:
            holder_desc = (
                f"PID {pid} ({'running' if alive else 'not running — likely stale'})"
                if pid is not None
                else "unknown PID"
            )
            hint = (
                "Wait for the active package manager to finish."
                if alive
                else f"No live process holds it; if no pacman process is running, remove {PACMAN_DB_LOCK}."
            )
            raise PacmanLockError(
                f"Pacman database is locked ({PACMAN_DB_LOCK}, {holder_desc}). {hint}"
            )

        if not notified:
            who = f" (PID {pid})" if pid is not None else ""
            ui.info(f"Waiting up to {wait_seconds}s for pacman database lock{who} to clear...")
            notified = True
        time.sleep(0.5)


def _urlopen_with_retry(
    req: urllib.request.Request,
    *,
    ssl_ctx: ssl.SSLContext,
    timeout: float,
    ui: ConsoleUI,
) -> Any:
    """Open an HTTP request with bounded exponential backoff on transient failures."""
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return urllib.request.urlopen(req, context=ssl_ctx, timeout=timeout)
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code != 429 and exc.code < 500:
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_exc = exc

        if attempt < MAX_RETRIES:
            delay = 0.5 * (2 ** (attempt - 1))
            ui.debug(
                f"Transient network error ({last_exc}); retrying in {delay:.1f}s ({attempt}/{MAX_RETRIES})..."
            )
            time.sleep(delay)

    assert last_exc is not None
    raise last_exc


def fetch_manifest(
    repo_url: str,
    *,
    bypass_cache: bool,
    ui: ConsoleUI,
    timeout: float = 15.0,
) -> tuple[str, PackageCatalog]:
    base_manifest = f"{repo_url.rstrip('/')}/manifest.json"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }

    if bypass_cache:
        nonce = f"{int(time.time())}_{secrets.token_hex(4)}"
        parsed = urllib.parse.urlsplit(base_manifest)
        query = urllib.parse.urlencode({"cb": nonce})
        manifest_url = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, query, "")
        )
        headers["Cache-Control"] = "no-cache, no-store, max-age=0"
        headers["Pragma"] = "no-cache"
    else:
        manifest_url = base_manifest

    ui.debug(f"Fetching manifest: {manifest_url}")
    req = urllib.request.Request(manifest_url, headers=headers)

    try:
        with _urlopen_with_retry(
            req, ssl_ctx=build_ssl_context(), timeout=timeout, ui=ui
        ) as resp:
            raw = resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ManifestError(f"Failed to fetch manifest from {base_manifest}: {exc}") from exc

    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not isinstance(payload.get("packages"), dict):
            raise ManifestError("Invalid manifest structure: missing 'packages' object.")
        generated_at = str(payload.get("generated_at", "unknown"))
        catalog: PackageCatalog = {
            name: RemotePackage.from_dict(name, pkg_data)
            for name, pkg_data in payload["packages"].items()
        }
    except json.JSONDecodeError as exc:
        raise ManifestError(f"Invalid JSON in remote manifest: {exc}") from exc

    if not catalog:
        raise ManifestError("Remote manifest contains zero packages.")

    return generated_at, catalog


def _read_local_build_date(pkgname: str, pkgver: str) -> int | None:
    """Read raw integer %BUILDDATE% from /var/lib/pacman/local/<pkgname>-<pkgver>/desc."""
    desc_path = PACMAN_LOCAL_DB / f"{pkgname}-{pkgver}" / "desc"
    try:
        lines = desc_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for idx, line in enumerate(lines):
            if line.strip() == "%BUILDDATE%" and idx + 1 < len(lines):
                val = lines[idx + 1].strip()
                if val.isdigit():
                    return int(val)
    except OSError:
        pass
    return None


def _read_local_source_sha256(pkgname: str) -> str | None:
    """Read installed source fingerprint from /usr/share/<pkgname>/source.sha256."""
    hash_path = Path("/usr/share") / pkgname / "source.sha256"
    try:
        tokens = hash_path.read_text(encoding="utf-8", errors="ignore").strip().split()
        if tokens and SHA256_RE.fullmatch(tokens[0]):
            return tokens[0].lower()
    except OSError:
        pass
    return None


def get_local_states(pkgnames: list[str]) -> dict[str, LocalPackageState]:
    """Query local pacman versions, build timestamps, and installed source hashes."""
    if not pkgnames:
        return {}

    proc = subprocess.run(
        ["pacman", "-Q", *pkgnames],
        capture_output=True,
        text=True,
        check=False,
    )
    states: dict[str, LocalPackageState] = {}
    for line in proc.stdout.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2:
            name, ver = parts
            states[name] = LocalPackageState(
                pkgname=name,
                pkgver=ver,
                build_date=_read_local_build_date(name, ver),
                source_sha256=_read_local_source_sha256(name),
            )
    return states


def vercmp(ver_a: str, ver_b: str) -> int:
    """Compare two Arch package versions using pacman's native `vercmp` binary."""
    proc = subprocess.run(
        ["vercmp", ver_a, ver_b],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise PacmanError(f"vercmp failed ({ver_a} vs {ver_b}): {proc.stderr.strip()}")
    return int(proc.stdout.strip())


def resolve_plans(
    catalog: PackageCatalog,
    targets: list[str],
    *,
    host_arch: str,
    force: bool,
    ui: ConsoleUI,
) -> list[PackagePlan]:
    if targets:
        unknown = [t for t in targets if t not in catalog]
        if unknown:
            available = ", ".join(sorted(catalog))
            raise ManifestError(
                f"Unknown package(s): {', '.join(unknown)}. Available: {available}"
            )
        selected = [catalog[t] for t in dict.fromkeys(targets)]
    else:
        selected = [catalog[k] for k in sorted(catalog)]

    for pkg in selected:
        if pkg.arch not in {"any", host_arch}:
            raise ManifestError(
                f"Architecture mismatch for {pkg.pkgname}: package is '{pkg.arch}', host is '{host_arch}'."
            )

    local_states = get_local_states([pkg.pkgname for pkg in selected])
    plans: list[PackagePlan] = []

    for pkg in selected:
        local = local_states.get(pkg.pkgname)
        if local is None:
            action = PackageAction.INSTALL
            reason = "not installed"
        else:
            cmp = vercmp(pkg.pkgver, local.pkgver)
            if cmp > 0:
                action = PackageAction.UPGRADE
                reason = f"newer version ({local.pkgver} -> {pkg.pkgver})"
            elif cmp == 0:
                if (
                    pkg.source_sha256 is not None
                    and local.source_sha256 is not None
                    and not hmac.compare_digest(pkg.source_sha256, local.source_sha256)
                ):
                    action = PackageAction.REBUILD_UPGRADE
                    reason = (
                        f"source changed at {pkg.pkgver} "
                        f"({local.source_sha256[:8]}.. -> {pkg.source_sha256[:8]}..)"
                    )
                elif (
                    (pkg.source_sha256 is None or local.source_sha256 is None)
                    and local.build_date is not None
                    and pkg.build_date > local.build_date
                ):
                    action = PackageAction.REBUILD_UPGRADE
                    reason = f"newer build timestamp at {pkg.pkgver}"
                elif force:
                    action = PackageAction.REINSTALL
                    reason = "forced reinstall"
                else:
                    action = PackageAction.UP_TO_DATE
                    reason = "up to date"
            else:
                if force:
                    action = PackageAction.REINSTALL
                    reason = f"forced downgrade ({local.pkgver} -> {pkg.pkgver})"
                else:
                    action = PackageAction.DOWNGRADE_SKIPPED
                    reason = f"local version newer ({local.pkgver} > {pkg.pkgver})"

        ui.debug(f"Plan {pkg.pkgname}: {action.value} ({reason})")
        plans.append(
            PackagePlan(
                pkgname=pkg.pkgname,
                action=action,
                reason=reason,
                local_ver=local.pkgver if local is not None else None,
                remote_ver=pkg.pkgver,
                arch=pkg.arch,
                size_bytes=pkg.size_bytes,
                filename=pkg.filename,
                sha256=pkg.sha256,
            )
        )

    return plans


def format_bytes(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024.0 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    return f"{num_bytes} B"


def download_and_verify(
    repo_url: str,
    plans: list[PackagePlan],
    staging_dir: Path,
    ui: ConsoleUI,
) -> list[Path]:
    """Download queued packages into tmpfs `.part` files, verify SHA-256, and rename atomically."""
    staged_files: list[Path] = []
    ssl_ctx = build_ssl_context()

    rich_progress_cls = None
    if ui.is_tty:
        try:
            from rich.console import Console
            from rich.progress import (
                BarColumn,
                DownloadColumn,
                Progress,
                TextColumn,
                TimeRemainingColumn,
                TransferSpeedColumn,
            )

            rich_progress_cls = (
                Console,
                Progress,
                TextColumn,
                BarColumn,
                DownloadColumn,
                TransferSpeedColumn,
                TimeRemainingColumn,
            )
        except ImportError:
            rich_progress_cls = None

    if rich_progress_cls is not None:
        (
            Console,
            Progress,
            TextColumn,
            BarColumn,
            DownloadColumn,
            TransferSpeedColumn,
            TimeRemainingColumn,
        ) = rich_progress_cls

        progress = Progress(
            TextColumn("[bold cyan]{task.description:<18}"),
            BarColumn(bar_width=28),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=Console(stderr=True),
            transient=False,
        )
        with progress:
            ui.attach_rich_console(progress.console)
            try:
                for plan in plans:
                    task_id = progress.add_task(plan.pkgname, total=plan.size_bytes)
                    dest = _stream_single_package(
                        repo_url=repo_url,
                        plan=plan,
                        staging_dir=staging_dir,
                        ssl_ctx=ssl_ctx,
                        ui=ui,
                        on_chunk=lambda n, tid=task_id: progress.update(tid, advance=n),
                    )
                    staged_files.append(dest)
            finally:
                ui.detach_rich_console()
    else:
        for plan in plans:
            downloaded = 0

            def ansi_progress(
                n: int, *, pkg: str = plan.pkgname, total: int = plan.size_bytes
            ) -> None:
                nonlocal downloaded
                downloaded += n
                if ui.is_tty and total > 0:
                    pct = min(100, int((downloaded * 100) / total))
                    filled = pct // 4
                    bar = "█" * filled + "░" * (25 - filled)
                    print(
                        f"\r  {pkg:<18} [{bar}] {pct:3d}% ({format_bytes(downloaded)}/{format_bytes(total)})",
                        end="",
                        file=sys.stderr,
                        flush=True,
                    )

            dest = _stream_single_package(
                repo_url=repo_url,
                plan=plan,
                staging_dir=staging_dir,
                ssl_ctx=ssl_ctx,
                ui=ui,
                on_chunk=ansi_progress,
            )
            if ui.is_tty:
                print(file=sys.stderr)
            staged_files.append(dest)

    return staged_files


def _stream_single_package(
    *,
    repo_url: str,
    plan: PackagePlan,
    staging_dir: Path,
    ssl_ctx: ssl.SSLContext,
    ui: ConsoleUI,
    on_chunk: Callable[[int], None],
) -> Path:
    url = f"{repo_url.rstrip('/')}/{plan.filename.lstrip('/')}"
    safe_name = PurePosixPath(plan.filename).name
    dest_path = staging_dir / safe_name
    part_path = staging_dir / f".{safe_name}.part"

    resolved_root = staging_dir.resolve()
    if dest_path.resolve().parent != resolved_root or part_path.resolve().parent != resolved_root:
        raise ManifestError(f"Path traversal blocked for {plan.filename!r}")

    ui.debug(f"Downloading {url} -> {part_path}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with (
            _urlopen_with_retry(req, ssl_ctx=ssl_ctx, timeout=30.0, ui=ui) as resp,
            part_path.open("wb") as out_file,
        ):
            while chunk := resp.read(CHUNK_SIZE):
                out_file.write(chunk)
                on_chunk(len(chunk))
    except BaseException as exc:
        part_path.unlink(missing_ok=True)
        if isinstance(exc, (urllib.error.URLError, TimeoutError, OSError)):
            raise DuskySyncError(f"Download failed for {plan.pkgname} ({url}): {exc}") from exc
        raise

    try:
        actual_size = part_path.stat().st_size
        if actual_size != plan.size_bytes:
            raise ChecksumMismatchError(
                f"Size mismatch for {plan.pkgname}: expected {plan.size_bytes} B, got {actual_size} B"
            )

        with part_path.open("rb") as verify_file:
            actual_sha256 = hashlib.file_digest(verify_file, "sha256").hexdigest()

        if not hmac.compare_digest(actual_sha256.lower(), plan.sha256.lower()):
            raise ChecksumMismatchError(
                f"SHA-256 mismatch for {plan.pkgname}:\n"
                f"  expected: {plan.sha256}\n"
                f"  actual:   {actual_sha256}"
            )

        part_path.replace(dest_path)
    except BaseException:
        part_path.unlink(missing_ok=True)
        raise

    ui.debug(f"Verified {plan.pkgname} ({actual_sha256[:16]}...)")
    return dest_path


def install_packages(
    pkg_paths: list[Path],
    *,
    non_interactive: bool,
    wait_lock: int,
    ui: ConsoleUI,
) -> None:
    """Install staged packages atomically in a single `pacman -U` transaction."""
    if not pkg_paths:
        return

    check_pacman_lock(wait_lock, ui)

    cmd: list[str] = []
    if os.geteuid() != 0:
        sudo_bin = shutil.which("sudo")
        doas_bin = shutil.which("doas")
        if sudo_bin is not None:
            cmd.append(sudo_bin)
            if non_interactive or ui.json_mode or not sys.stdin.isatty():
                cmd.append("-n")
        elif doas_bin is not None:
            cmd.append(doas_bin)
            if non_interactive or ui.json_mode or not sys.stdin.isatty():
                cmd.append("-n")
        else:
            raise PacmanError(
                "Root privileges required to run pacman -U, and neither 'sudo' nor 'doas' was found."
            )

    cmd.extend(["pacman", "-U", "--noconfirm", *(str(p) for p in pkg_paths)])
    ui.debug(f"Executing: {' '.join(cmd)}")

    # Mask signals in the supervisor during `pacman -U` so TemporaryDirectory
    # never unlinks staged archives while pacman is reading them or terminal closes.
    guarded_signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    prev_handlers = {sig: signal.getsignal(sig) for sig in guarded_signals}
    for sig in guarded_signals:
        signal.signal(sig, signal.SIG_IGN)
    try:
        if ui.json_mode:
            proc = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            if proc.stdout and ui.verbose:
                sys.stderr.write(proc.stdout)
            if proc.returncode != 0:
                tail = "\n".join((proc.stdout or "").strip().splitlines()[-10:])
                detail = f": {tail}" if tail else ""
                raise PacmanError(f"pacman -U exited with status {proc.returncode}{detail}")
        else:
            proc = subprocess.run(cmd, check=False)
            if proc.returncode != 0:
                raise PacmanError(f"pacman -U exited with status {proc.returncode}")
    finally:
        for sig, handler in prev_handlers.items():
            signal.signal(sig, handler)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dusky-pkg-sync",
        description="Sync and install precompiled Dusky Arch Linux packages.",
    )
    parser.add_argument(
        "packages",
        nargs="*",
        help="Specific dusky package names to sync (default: all packages in manifest).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Dry-run mode: check available updates without downloading or installing.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reinstall packages even if the installed version and source hash match remote.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Fail immediately if privilege escalation (sudo/doas) requires a password.",
    )
    parser.add_argument(
        "--wait-lock",
        type=int,
        default=30,
        metavar="SECONDS",
        help="Seconds to wait if /var/lib/pacman/db.lck is held (default: 30; 0 to fail immediately).",
    )
    parser.add_argument(
        "--allow-cache",
        action="store_true",
        help="Allow Fastly CDN cached manifest responses instead of bypassing cache.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable detailed diagnostic output.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON summary to stdout for parent updater scripts.",
    )
    parser.add_argument(
        "--warn-only",
        "--lenient",
        action="store_true",
        help="Log warnings on network or sync errors and exit with code 0 instead of failing.",
    )
    parser.add_argument(
        "--installed-only",
        action="store_true",
        help="Only upgrade/sync packages that are already installed locally.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        metavar="SECONDS",
        help="HTTP timeout in seconds for manifest and package requests (default: 15.0).",
    )
    parser.add_argument(
        "--repo-url",
        default=os.environ.get("DUSKY_REPO_URL", DEFAULT_REPO_URL),
        help=f"Base raw repository URL (default: {DEFAULT_REPO_URL}).",
    )
    return parser.parse_args(argv)


def emit_json(stream: TextIO, payload: dict[str, Any]) -> None:
    stream.write(json.dumps(payload, indent=2) + "\n")
    stream.flush()


def _handle_sigterm(signum: int, _frame: Any) -> None:
    raise TerminatedError(f"Terminated by signal {signum}.")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    json_out: TextIO = sys.stdout
    if args.json:
        sys.stdout = sys.stderr

    signal.signal(signal.SIGTERM, _handle_sigterm)
    ui = ConsoleUI(json_mode=args.json, verbose=args.verbose)

    try:
        host_arch = preflight_checks()
        generated_at, catalog = fetch_manifest(
            args.repo_url,
            bypass_cache=not args.allow_cache,
            ui=ui,
            timeout=args.timeout,
        )
        plans = resolve_plans(
            catalog,
            args.packages,
            host_arch=host_arch,
            force=args.force,
            ui=ui,
        )
        if args.installed_only:
            plans = [p for p in plans if p.local_ver is not None]
        queued = [p for p in plans if p.needs_download]

        if not args.json:
            ui.info(f"Dusky Package Repository (manifest: {generated_at})")
            for p in plans:
                local_disp = p.local_ver or "not installed"
                match p.action:
                    case PackageAction.INSTALL:
                        tag = f"\033[32m[install]\033[0m     {p.remote_ver} ({format_bytes(p.size_bytes)})"
                    case PackageAction.UPGRADE:
                        tag = f"\033[33m[upgrade]\033[0m     {local_disp} -> {p.remote_ver} ({format_bytes(p.size_bytes)})"
                    case PackageAction.REBUILD_UPGRADE:
                        tag = f"\033[33m[rebuilt]\033[0m     {p.remote_ver} ({p.reason}; {format_bytes(p.size_bytes)})"
                    case PackageAction.REINSTALL:
                        tag = f"\033[36m[reinstall]\033[0m   {p.remote_ver} ({format_bytes(p.size_bytes)})"
                    case PackageAction.UP_TO_DATE:
                        tag = f"\033[2m[up-to-date]\033[0m  {local_disp}"
                    case PackageAction.DOWNGRADE_SKIPPED:
                        tag = f"\033[35m[newer-local]\033[0m {local_disp} > {p.remote_ver} (use --force)"
                ui.info(f"  {p.pkgname:<18} {tag}")

        if args.check:
            if args.json:
                emit_json(
                    json_out,
                    {
                        "status": "ok",
                        "mode": "check",
                        "manifest_generated_at": generated_at,
                        "updates_available": len(queued),
                        "total_download_bytes": sum(p.size_bytes for p in queued),
                        "packages": [asdict(p) for p in plans],
                    },
                )
            return 0

        if not queued:
            ui.info("\nAll target Dusky packages are up to date.")
            if args.json:
                emit_json(
                    json_out,
                    {
                        "status": "ok",
                        "mode": "sync",
                        "manifest_generated_at": generated_at,
                        "installed_count": 0,
                        "packages": [asdict(p) for p in plans],
                    },
                )
            return 0

        check_pacman_lock(args.wait_lock, ui)

        total_bytes = sum(p.size_bytes for p in queued)
        ui.info(
            f"\nDownloading {len(queued)} package(s) ({format_bytes(total_bytes)} total)..."
        )

        with tempfile.TemporaryDirectory(prefix="dusky-sync-", dir="/tmp") as tmp_dir:
            staged_paths = download_and_verify(
                repo_url=args.repo_url,
                plans=queued,
                staging_dir=Path(tmp_dir),
                ui=ui,
            )
            ui.info("Installing verified packages atomically via pacman...")
            install_packages(
                staged_paths,
                non_interactive=args.non_interactive,
                wait_lock=args.wait_lock,
                ui=ui,
            )

        ui.info("Sync complete.")
        if args.json:
            emit_json(
                json_out,
                {
                    "status": "ok",
                    "mode": "sync",
                    "manifest_generated_at": generated_at,
                    "installed_count": len(queued),
                    "packages": [asdict(p) for p in plans],
                },
            )
        return 0

    except KeyboardInterrupt:
        if args.json:
            emit_json(
                json_out,
                {
                    "status": "error",
                    "error_type": "KeyboardInterrupt",
                    "message": "Aborted by user.",
                },
            )
        else:
            ui.error("Aborted by user.")
        return 130
    except TerminatedError as exc:
        if args.json:
            emit_json(
                json_out,
                {
                    "status": "error",
                    "error_type": "TerminatedError",
                    "message": str(exc),
                },
            )
        else:
            ui.error(str(exc))
        return 143
    except DuskySyncError as exc:
        if args.warn_only:
            if args.json:
                emit_json(
                    json_out,
                    {
                        "status": "warning",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                )
            else:
                ui.warn(f"Warning: {exc} (skipping package sync; setup scripts will fall back to local builds)")
            return 0
        if args.json:
            emit_json(
                json_out,
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            )
        else:
            ui.error(str(exc))
        return 1
    except Exception as exc:
        if args.warn_only:
            if args.json:
                emit_json(
                    json_out,
                    {
                        "status": "warning",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                )
            else:
                ui.warn(f"Warning: unexpected sync error: {exc} (skipping package sync)")
            return 0
        raise


if __name__ == "__main__":
    sys.exit(main())
