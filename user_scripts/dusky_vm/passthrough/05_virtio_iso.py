#!/usr/bin/env python3
"""
Arsonix KVM/VFIO Pipeline -- Phase 1 (05_virtio_iso.py)
Hypervisor staging: packages, KVM capability gate, group membership, VirtIO media.

Target : Arch Linux / Linux 7.3+ / Python 3.15+ / systemd 262+
Policy : Zero legacy. Idempotent. Atomic. Strict. One job: STAGE THE HOST.

"""

import argparse
import grp
import hashlib
import json
import os
import pwd
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Never

# ==============================================================================
# PRE-FLIGHT (stdlib only -- runs before any third-party import)
# ==============================================================================
MIN_PY: tuple[int, int, int] = (3, 15, 0)
STATE_DIR = Path("/var/lib/arsonix")
STATE_FILE = STATE_DIR / "state.json"
STATE_SCHEMA = 2
PACMAN_LCK = Path("/var/lib/pacman/db.lck")
SYNC_DB_DIR = Path("/var/lib/pacman/sync")
MAX_DB_AGE_S = 7 * 24 * 3600

VIRTIO_URL = (
    "https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/"
    "stable-virtio/virtio-win.iso"
)


def _hard_exit(msg: str) -> Never:
    sys.stderr.write(f"\n[FATAL] {msg}\n\n")
    raise SystemExit(1)


def require_python() -> None:
    if sys.version_info[:3] < MIN_PY:
        _hard_exit(
            f"Python {MIN_PY[0]}.{MIN_PY[1]}.{MIN_PY[2]}+ required; running "
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}."
        )


def elevate() -> None:
    """Re-exec through sudo exactly once. Loop-guarded, PATH-verified."""
    if os.geteuid() == 0:
        return
    if os.environ.get("ARSONIX_ELEVATED") == "1":
        _hard_exit("Re-exec loop detected: sudo did not yield uid 0.")
    sudo = shutil.which("sudo")
    if sudo is None:
        _hard_exit("'sudo' is not in PATH. Re-run this script as root.")
    try:
        script = Path(sys.argv[0]).resolve(strict=True)
    except OSError as exc:
        _hard_exit(f"Cannot resolve own script path: {exc}")
    sys.stderr.write("\n[INFO] Elevating via sudo (single re-exec)...\n")
    os.environ["ARSONIX_ELEVATED"] = "1"
    os.execv(
        sudo,
        [sudo, "--preserve-env=ARSONIX_ELEVATED", "--", sys.executable, str(script), *sys.argv[1:]],
    )


def wait_for_pacman_lock(limit_s: int = 90) -> None:
    """Block on db.lck instead of failing instantly; abort if it never clears."""
    if not PACMAN_LCK.exists():
        return
    sys.stderr.write(f"[INFO] {PACMAN_LCK} present; waiting up to {limit_s}s...\n")
    deadline = time.monotonic() + limit_s
    while time.monotonic() < deadline:
        if not PACMAN_LCK.exists():
            return
        time.sleep(1.0)
    _hard_exit(
        f"{PACMAN_LCK} held for >{limit_s}s. Close other package managers. "
        "If the lock is stale (no pacman/paru process alive), remove it manually."
    )


require_python()
elevate()
wait_for_pacman_lock()

# ==============================================================================
# BOOTSTRAP: rich (the only third-party dependency of the pipeline)
# ==============================================================================
try:
    import rich  # noqa: F401
except ModuleNotFoundError:
    sys.stderr.write("==> Bootstrapping python-rich from [extra]...\n")
    boot = subprocess.run(
        ["pacman", "-S", "--needed", "--noconfirm", "python-rich"],
        stdout=subprocess.DEVNULL,
        check=False,
    )
    if boot.returncode != 0:
        _hard_exit("Could not install python-rich. Check mirrors / run 'pacman -Syu'.")
    import importlib

    importlib.invalidate_caches()
    import rich  # noqa: F401

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.prompt import Confirm, Prompt
from rich.table import Table

console = Console(force_terminal=True, force_interactive=True, soft_wrap=False)

# ==============================================================================
# SHARED PRIMITIVES (duplicated verbatim across phases: every file is standalone)
# ==============================================================================


@dataclass(frozen=True, slots=True)
class Cmd:
    argv: list[str]
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0


def run(
    argv: list[str],
    *,
    check: bool = False,
    timeout: float = 300.0,
    capture: bool = True,
    stdin_null: bool = True,
) -> Cmd:
    """Single choke point for subprocesses. Never swallows failure silently."""
    try:
        proc = subprocess.run(
            argv,
            check=False,
            timeout=timeout,
            text=True,
            capture_output=capture,
            stdin=subprocess.DEVNULL if stdin_null else None,
        )
        res = Cmd(argv, proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip())
    except subprocess.TimeoutExpired:
        res = Cmd(argv, 124, "", f"timeout after {timeout}s")
    except FileNotFoundError:
        res = Cmd(argv, 127, "", f"executable not found: {argv[0]}")
    if check and not res.ok:
        bail(f"Command failed (rc={res.code}): {' '.join(argv)}\n{res.err or res.out}")
    return res


def bail(msg: str) -> Never:
    console.print(Panel(f"[bold red]FATAL[/bold red]\n{msg}", border_style="red"))
    raise SystemExit(1)


def atomic_write(path: Path, content: str, *, mode: int | None = None) -> bool:
    """
    Write via mkstemp in the target directory + os.replace (POSIX-atomic rename).
    Returns False when the on-disk bytes already match (true idempotency).
    Preserves existing mode/uid/gid unless 'mode' overrides.
    """
    keep_mode = 0o644 if mode is None else mode
    uid, gid = 0, 0
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == content:
                return False
        except (UnicodeDecodeError, OSError):
            pass
        st = path.stat()
        keep_mode = stat.S_IMODE(st.st_mode) if mode is None else mode
        uid, gid = st.st_uid, st.st_gid
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".arsonix")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, keep_mode)
        os.chown(tmp, uid, gid)
        os.replace(tmp, path)
        dir_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return True
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def state_load() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"schema": STATE_SCHEMA}
    return data if isinstance(data, dict) else {"schema": STATE_SCHEMA}


def state_merge(**kv: object) -> None:
    """Read-modify-write the cross-phase state file. Survives reboots (unlike /tmp)."""
    data = state_load()
    data.update(kv)
    data["schema"] = STATE_SCHEMA
    data["updated"] = datetime.now(UTC).isoformat(timespec="seconds")
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o755)
    atomic_write(STATE_FILE, json.dumps(data, indent=2, sort_keys=True) + "\n", mode=0o644)


def resolve_operator() -> pwd.struct_passwd:
    """Forensically resolve the human behind sudo/doas/pkexec/logind."""
    candidates: list[str] = []
    for env_key in ("SUDO_USER", "DOAS_USER"):
        val = os.environ.get(env_key, "").strip()
        if val:
            candidates.append(val)
    pkexec_uid = os.environ.get("PKEXEC_UID", "").strip()
    if pkexec_uid.isdigit():
        try:
            candidates.append(pwd.getpwuid(int(pkexec_uid)).pw_name)
        except KeyError:
            pass
    try:
        candidates.append(os.getlogin())
    except OSError:
        pass
    seat = run(["loginctl", "list-users", "--no-legend"], timeout=10)
    if seat.ok:
        for line in seat.out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] != "root":
                candidates.append(parts[1])

    for name in candidates:
        if not name or name == "root":
            continue
        try:
            entry = pwd.getpwnam(name)
        except KeyError:
            continue
        if entry.pw_uid >= 1000:
            return entry

    console.print("[yellow]! Could not infer the unprivileged operator from the session.[/yellow]")
    while True:
        name = Prompt.ask("[bold cyan]Non-root Arch username[/bold cyan]").strip()
        try:
            return pwd.getpwnam(name)
        except KeyError:
            console.print(f"[red]  x '{name}' is not in the passwd database.[/red]")


# ==============================================================================
# PACKAGE PLANE -- repo/AUR split resolved dynamically against the live sync db
# ==============================================================================
REPO_PACKAGES: list[str] = [
    "qemu-desktop",       # RETAINED: superset for passthrough incl. spice/gtk/virtio-gpu; qemu-full (all foreign arches) not needed
    "libvirt",            # 12.6+ modular daemons (virtqemud etc.)
    "virt-install",       # virt-install / virt-xml / virt-clone
    "virt-manager",
    "virt-viewer",
    "virtiofsd",          # host backend for optional Linux guest shared folders
    "acl",               # shared-directory access and inherited operator permissions
    "openssh",           # host-to-guest SSH and Wayland clipboard transport
    "wl-clipboard",      # native Wayland clipboard integration
    "dnsmasq",            # libvirt NAT/DHCP backend
    "edk2-ovmf",          # firmware descriptors /usr/share/qemu/firmware/*.json (libvirt selects blob via JSON, no hard-coded fd)
    "swtpm",              # TPM 2.0 emulation (mandatory for win11 osinfo)
    "nftables",           # libvirt >= 10.3 native firewall_backend=nftables (no iptables shim)
    "libosinfo",          # osinfo-db for --osinfo
    "pciutils",
    "dmidecode",
    "python-rich",
]


def pacman_db_is_fresh() -> bool:
    if not SYNC_DB_DIR.is_dir():
        return False
    dbs = list(SYNC_DB_DIR.glob("*.db"))
    if not dbs:
        return False
    newest = max(db.stat().st_mtime for db in dbs)
    return (time.time() - newest) < MAX_DB_AGE_S


def repo_index() -> set[str]:
    res = run(["pacman", "-Slq"], timeout=60)
    return set(res.out.split()) if res.ok else set()


def split_repo_aur(pkgs: list[str]) -> tuple[list[str], list[str]]:
    index = repo_index()
    repo: list[str] = []
    aur: list[str] = []
    for pkg in pkgs:
        if pkg in index or run(["pacman", "-Si", pkg], timeout=30).ok:
            repo.append(pkg)
        else:
            aur.append(pkg)
    return repo, aur


def installed(pkg: str) -> bool:
    return run(["pacman", "-Qq", pkg], timeout=20).ok


def install_repo(pkgs: list[str]) -> None:
    missing = [p for p in pkgs if not installed(p)]
    if not missing:
        console.print("[bold green]  ok[/bold green] All repository packages already present.")
        return
    console.print(f"  [cyan]pacman -S --needed[/cyan] {' '.join(missing)}")
    proc = subprocess.run(
        ["pacman", "-S", "--needed", "--noconfirm", *missing], check=False, stdin=subprocess.DEVNULL
    )
    if proc.returncode != 0:
        bail(f"pacman transaction failed (rc={proc.returncode}).")
    console.print("[bold green]  ok[/bold green] Repository packages staged.")


def install_aur(pkgs: list[str], operator: pwd.struct_passwd) -> bool:
    """paru refuses to run as uid 0; drop privileges deterministically."""
    if not pkgs:
        return True
    paru = shutil.which("paru")
    if paru is None:
        console.print("[yellow]  ! 'paru' absent from PATH; AUR stage skipped.[/yellow]")
        return False
    console.print(f"  [cyan]paru (as {operator.pw_name})[/cyan] {' '.join(pkgs)}")
    proc = subprocess.run(
        [
            "sudo", "-u", operator.pw_name, "--",
            paru, "-S", "--needed", "--noconfirm", "--skipreview", "--removemake", *pkgs,
        ],
        check=False,
    )
    return proc.returncode == 0


# ==============================================================================
# HOST CAPABILITY GATE
# ==============================================================================
def verify_kvm_capability() -> None:
    console.print("\n[bold blue]==>[/bold blue] [bold]Verifying hardware virtualization[/bold]")
    flags: set[str] = set()
    for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("flags"):
            flags = set(line.split(":", 1)[1].split())
            break
    if not flags & {"vmx", "svm"}:
        bail(
            "Neither VT-x (vmx) nor AMD-V (svm) is exposed by the CPU.\n"
            "Enable SVM / VT-x + IOMMU (AMD-Vi / VT-d) in firmware setup, then re-run."
        )
    if not Path("/dev/kvm").exists():
        bail("/dev/kvm is absent. Load kvm_intel/kvm_amd or check firmware settings.")
    vendor = "AMD-V (svm)" if "svm" in flags else "Intel VT-x (vmx)"
    console.print(f"[bold green]  ok[/bold green] {vendor} present, /dev/kvm live.")
    if not Path("/sys/class/iommu").is_dir() or not any(Path("/sys/class/iommu").iterdir()):
        console.print(
            "[yellow]  ! No IOMMU exposed yet. Expected before Phase 3 "
            "(intel_iommu=on / iommu=pt not yet injected).[/yellow]"
        )


# ==============================================================================
# GROUP MEMBERSHIP (idempotent -- no needless usermod, no needless warning)
# ==============================================================================
def configure_groups(operator: pwd.struct_passwd) -> None:
    console.print(f"\n[bold blue]==>[/bold blue] [bold]Access control for '{operator.pw_name}'[/bold]")
    wanted = ["libvirt", "kvm", "input"]
    known = {g.gr_name for g in grp.getgrall()}
    for group in wanted:
        if group not in known:
            bail(f"System group '{group}' missing. libvirt/systemd sysusers did not run.")

    current = {g.gr_name for g in grp.getgrall() if operator.pw_name in g.gr_mem}
    current.add(grp.getgrgid(operator.pw_gid).gr_name)
    add = [g for g in wanted if g not in current]
    if not add:
        console.print("[bold green]  ok[/bold green] Membership already correct; no changes.")
        return
    run(["usermod", "-aG", ",".join(add), operator.pw_name], check=True, timeout=30)
    console.print(f"[bold green]  ok[/bold green] Added to: {', '.join(add)}")
    console.print(
        Panel(
            f"'{operator.pw_name}' must terminate every session (logout / reboot) before the "
            "new supplementary groups appear in the credential set.\n"
            "Verify with:  id -nG",
            title="Credential refresh required",
            border_style="yellow",
        )
    )


# ==============================================================================
# VIRTIO MEDIA
# ==============================================================================
def aur_virtio_iso() -> Path | None:
    """Ask pacman where the AUR package actually put the ISO. Never guess a path."""
    res = run(["pacman", "-Qlq", "virtio-win"], timeout=30)
    if not res.ok:
        return None
    for line in res.out.splitlines():
        candidate = Path(line.strip())
        if candidate.suffix == ".iso" and candidate.is_file():
            return candidate
    return None


def human(n: int) -> str:
    step = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if step < 1024.0:
            return f"{step:.1f} {unit}"
        step /= 1024.0
    return f"{step:.1f} TiB"


def download_iso(url: str, dest: Path, timeout: float) -> None:
    """
    Chunked stream to <dest>.part, then os.replace. Resumable via HTTP Range.
    A half-written ISO can never appear at the destination path.
    """
    part = dest.with_suffix(dest.suffix + ".part")
    offset = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": "arsonix-vfio/2026.08", "Accept-Encoding": "identity"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
        console.print(f"  [cyan]Resuming at {human(offset)}[/cyan]")

    free = shutil.disk_usage(dest.parent).free
    req = urllib.request.Request(url, headers=headers, method="GET")
    digest = hashlib.sha256()
    if offset:
        with part.open("rb") as prev:
            for block in iter(lambda: prev.read(1 << 20), b""):
                digest.update(block)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            declared = int(resp.headers.get("Content-Length") or 0)
            total = declared + offset if resp.status == 206 else declared
            need = (total - offset) if total else 0
            if need and need + (32 << 20) > free:
                bail(
                    f"Insufficient space in {dest.parent}: need {human(need + (32 << 20))} "
                    f"(image + 32 MiB headroom), free {human(free)}."
                )
            if resp.status == 200 and offset:
                offset, digest = 0, hashlib.sha256()  # server ignored Range: restart clean
            mode = "ab" if offset else "wb"
            with (
                part.open(mode) as sink,
                Progress(
                    SpinnerColumn(style="cyan"),
                    TextColumn("[bold cyan]{task.fields[name]}"),
                    BarColumn(bar_width=None),
                    DownloadColumn(),
                    TransferSpeedColumn(),
                    TimeRemainingColumn(),
                    console=console,
                    transient=False,
                ) as progress,
            ):
                task = progress.add_task("dl", name=dest.name, total=total or None, completed=offset)
                while chunk := resp.read(1 << 14):  # 16 KiB
                    sink.write(chunk)
                    digest.update(chunk)
                    progress.update(task, advance=len(chunk))
                sink.flush()
                os.fsync(sink.fileno())
    except urllib.error.HTTPError as exc:
        bail(f"HTTP {exc.code} from {url}: {exc.reason}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        console.print(f"[yellow]  ! Stream interrupted: {exc}[/yellow]")
        console.print(f"[yellow]    Partial file kept at {part} -- re-run to resume.[/yellow]")
        bail("VirtIO ISO download did not complete.")

    # Size sanity floor: virtio-win is ~400 MiB; a stub <80 MB is torn
    if part.stat().st_size < 80_000_000:
        bail(f"Downloaded file at {part} is too small ({human(part.stat().st_size)}); likely truncated.")
    if total and part.stat().st_size != total:
        bail(f"Size mismatch: expected {human(total)}, got {human(part.stat().st_size)}.")
    os.replace(part, dest)
    os.chmod(dest, 0o644)
    # fsync parent dir to ensure atomic publish is durable
    dir_fd = os.open(dest.parent, os.O_DIRECTORY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    console.print(f"[bold green]  ok[/bold green] {dest} sha256={digest.hexdigest()[:16]}...")


class PathCompleter:
    """readline completer that restores the previous global state on exit."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self._prev_completer = None
        self._prev_delims = ""

    def expand(self, text: str) -> str:
        if text.startswith("~/"):
            return str(self.home / text[2:])
        if text == "~":
            return str(self.home)
        return text

    def __call__(self, text: str, state: int) -> str | None:
        base = Path(self.expand(text))
        parent = base.parent if text.endswith("/") is False else base
        try:
            pattern = base.name + "*" if not text.endswith("/") else "*"
            hits = sorted(str(p) + ("/" if p.is_dir() else "") for p in parent.glob(pattern))
        except OSError:
            return None
        return hits[state] if state < len(hits) else None

    def __enter__(self) -> "PathCompleter":
        import readline

        self._readline = readline
        self._prev_completer = readline.get_completer()
        self._prev_delims = readline.get_completer_delims()
        readline.set_completer_delims(" \t\n;")
        readline.parse_and_bind("tab: complete")
        readline.set_completer(self)
        return self

    def __exit__(self, *_exc: object) -> None:
        self._readline.set_completer(self._prev_completer)
        self._readline.set_completer_delims(self._prev_delims)


def stage_virtio(operator: pwd.struct_passwd, pool: Path, timeout: float) -> None:
    console.print("\n[bold blue]==>[/bold blue] [bold]Staging VirtIO-win media[/bold]")
    pool.mkdir(parents=True, exist_ok=True)
    target = pool / "virtio-win.iso"

    if target.is_file() and target.stat().st_size >= 80_000_000:
        console.print(f"[bold green]  ok[/bold green] VirtIO ISO already staged at {target}.")
        return
    # Local media must work without contacting AUR first. A packaged ISO may
    # already be installed; otherwise offer the local-path/download choice.
    source = aur_virtio_iso()

    if source is not None:
        if target.exists() or target.is_symlink():
            target.unlink()
        target.symlink_to(source)
        console.print(f"[bold green]  ok[/bold green] {target} -> {source}")
        return

    if target.is_symlink():  # dangling link from a removed AUR package
        target.unlink()

    console.print("[yellow]  ! No packaged VirtIO ISO found.[/yellow]")
    console.print("[dim]  Enter an absolute path to a local ISO (TAB completes), or press")
    console.print("  ENTER to stream the stable release from fedorapeople.org.[/dim]")
    with PathCompleter(Path(operator.pw_dir)) as completer:
        while True:
            try:
                raw = input("path > ").strip().strip("\"'")
            except EOFError:
                raw = ""
            except KeyboardInterrupt:
                console.print("\n[bold red]! Interrupted.[/bold red]")
                raise SystemExit(130) from None
            if raw == "":
                download_iso(VIRTIO_URL, target, timeout)
                return
            candidate = Path(completer.expand(raw))
            if candidate.is_file():
                if candidate.stat().st_size < 80_000_000:
                    console.print(f"[red]  x {candidate} is too small ({human(candidate.stat().st_size)}); not a valid virtio-win ISO.[/red]")
                    continue
                tmp = target.with_suffix(".iso.part")
                shutil.copyfile(candidate, tmp)
                # ensure durable atomic publish
                with tmp.open("rb") as f:
                    os.fsync(f.fileno())
                os.replace(tmp, target)
                os.chmod(target, 0o644)
                dir_fd = os.open(target.parent, os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
                console.print(f"[bold green]  ok[/bold green] Copied {candidate} -> {target}")
                return
            console.print("[red]  x Not a regular file. Try again.[/red]")


# ==============================================================================
# REPORT
# ==============================================================================
def summary(operator: pwd.struct_passwd, pool: Path) -> None:
    table = Table(title="Phase 1 -- staging result", header_style="bold magenta")
    table.add_column("Facet", style="cyan")
    table.add_column("Value")
    table.add_row("Operator", f"{operator.pw_name} (uid {operator.pw_uid})")
    table.add_row("Image pool", str(pool))
    iso = pool / "virtio-win.iso"
    if iso.exists():
        kind = "symlink -> " + str(iso.resolve()) if iso.is_symlink() else human(iso.stat().st_size)
        table.add_row("virtio-win.iso", kind)
    else:
        table.add_row("virtio-win.iso", "[yellow]not staged (Linux-only mode)[/yellow]")
    table.add_row("State file", str(STATE_FILE))
    console.print(table)


def main() -> None:
    parser = argparse.ArgumentParser(description="Arsonix Phase 1: hypervisor staging")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--virtio", action="store_true", help="stage VirtIO-win media")
    group.add_argument("--skip-virtio", action="store_true", help="Linux-only host")
    parser.add_argument("--pool", default="/var/lib/libvirt/images", help="libvirt image pool dir")
    parser.add_argument("--timeout", type=float, default=60.0, help="HTTP socket timeout (s)")
    args = parser.parse_args()

    console.clear()
    console.print(
        Panel(
            "[bold green]Arsonix Phase 1 -- Hypervisor Staging[/bold green]\n"
            "Arch rolling / Linux 7.3+ / Python 3.15+ / systemd 262+",
            expand=False,
            border_style="green",
        )
    )

    missing = [pkg for pkg in REPO_PACKAGES if not installed(pkg)]
    # An ISO containing the complete package set needs no transaction or sync
    # database lookup. Its offline setup must not expire merely with DB age.
    if missing and not pacman_db_is_fresh():
        bail(
            "The pacman sync database is missing or older than 7 days.\n"
            "Run a full system upgrade first (partial upgrades are unsupported on Arch):\n"
            "    sudo pacman -Syu"
        )

    operator = resolve_operator()
    console.print(f"[dim]operator = {operator.pw_name} ({operator.pw_dir})[/dim]")

    console.print("\n[bold blue]==>[/bold blue] [bold]Synchronizing hypervisor packages[/bold]")
    repo, aur = split_repo_aur(missing) if missing else ([], [])
    if aur:
        console.print(f"[yellow]  ! Not in any sync repo, deferring to AUR: {', '.join(aur)}[/yellow]")
    install_repo(repo)
    if aur:
        install_aur(aur, operator)

    verify_kvm_capability()

    if args.virtio:
        want = True
    elif args.skip_virtio:
        want = False
    else:
        want = Confirm.ask(
            "\n[bold cyan]Stage the Windows VirtIO driver ISO?[/bold cyan]", default=False
        )

    pool = Path(args.pool)
    if want:
        stage_virtio(operator, pool, args.timeout)
    else:
        pool.mkdir(parents=True, exist_ok=True)
        console.print("\n[yellow]VirtIO staging skipped (Linux-only mode).[/yellow]")

    configure_groups(operator)

    state_merge(
        human_user=operator.pw_name,
        human_uid=operator.pw_uid,
        human_home=operator.pw_dir,
        image_pool=str(pool),
        phase_05="complete",
    )

    summary(operator, pool)
    console.print("\n[bold green]=== PHASE 1 COMPLETE ===[/bold green]")
    console.print("Next: 07_storage_setup.py (storage topology + POSIX ACL traversal).\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n\n[bold red]! Interrupted by operator.[/bold red]\n")
        raise SystemExit(130) from None
