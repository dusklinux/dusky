#!/usr/bin/env python3
"""
Arsonix KVM/VFIO Pipeline -- Phase 1.5 (07_storage_setup.py)
Storage topology selection + POSIX.1e ACL traversal + cross-phase state.

Target : Arch Linux / Linux 7.3+ / Python 3.15+ / systemd 262+
Policy : Never hardcode the QEMU identity. Never claim success on a silent setfacl
         failure. Never persist pipeline state in world-writable /tmp.
"""

import argparse
import grp
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Never

MIN_PY: tuple[int, int, int] = (3, 15, 0)
STATE_DIR = Path("/var/lib/arsonix")
STATE_FILE = STATE_DIR / "state.json"
STATE_SCHEMA = 2
QEMU_CONF = Path("/etc/libvirt/qemu.conf")


def _hard_exit(msg: str) -> Never:
    sys.stderr.write(f"\n[FATAL] {msg}\n\n")
    raise SystemExit(1)


def require_python() -> None:
    if sys.version_info[:3] < MIN_PY:
        _hard_exit(f"Python {MIN_PY[0]}.{MIN_PY[1]}.{MIN_PY[2]}+ required.")


def elevate() -> None:
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


require_python()

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm, Prompt
    from rich.table import Table
except ModuleNotFoundError:
    _hard_exit("python-rich is missing. Run Phase 1 (05_virtio_iso.py) first.")

console = Console(force_terminal=True, force_interactive=True)


# ==============================================================================
# SHARED PRIMITIVES
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


def run(argv: list[str], *, check: bool = False, timeout: float = 120.0) -> Cmd:
    try:
        proc = subprocess.run(
            argv, check=False, timeout=timeout, text=True,
            capture_output=True, stdin=subprocess.DEVNULL,
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
    data = state_load()
    data.update(kv)
    data["schema"] = STATE_SCHEMA
    data["updated"] = datetime.now(UTC).isoformat(timespec="seconds")
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o755)
    atomic_write(STATE_FILE, json.dumps(data, indent=2, sort_keys=True) + "\n", mode=0o644)


def resolve_operator() -> pwd.struct_passwd:
    cached = state_load().get("human_user")
    candidates: list[str] = [str(cached)] if cached else []
    for env_key in ("SUDO_USER", "DOAS_USER"):
        val = os.environ.get(env_key, "").strip()
        if val:
            candidates.append(val)
    try:
        candidates.append(os.getlogin())
    except OSError:
        pass
    for name in candidates:
        if not name or name == "root":
            continue
        try:
            entry = pwd.getpwnam(name)
        except KeyError:
            continue
        if entry.pw_uid >= 1000:
            return entry
    while True:
        name = Prompt.ask("[bold cyan]Non-root Arch username[/bold cyan]").strip()
        try:
            return pwd.getpwnam(name)
        except KeyError:
            console.print(f"[red]  x '{name}' is not in the passwd database.[/red]")


class PathCompleter:
    def __init__(self, home: Path) -> None:
        self.home = home

    def expand(self, text: str) -> str:
        if text.startswith("~/"):
            return str(self.home / text[2:])
        return str(self.home) if text == "~" else text

    def __call__(self, text: str, state: int) -> str | None:
        base = Path(self.expand(text))
        parent = base if text.endswith("/") else base.parent
        pattern = "*" if text.endswith("/") else base.name + "*"
        try:
            hits = sorted(str(p) + ("/" if p.is_dir() else "") for p in parent.glob(pattern))
        except OSError:
            return None
        return hits[state] if state < len(hits) else None

    def __enter__(self) -> "PathCompleter":
        import readline

        self._rl = readline
        self._prev = readline.get_completer()
        self._delims = readline.get_completer_delims()
        readline.set_completer_delims(" \t\n;")
        readline.parse_and_bind("tab: complete")
        readline.set_completer(self)
        return self

    def __exit__(self, *_exc: object) -> None:
        self._rl.set_completer(self._prev)
        self._rl.set_completer_delims(self._delims)


# ==============================================================================
# QEMU IDENTITY -- resolved from libvirt, never assumed
# ==============================================================================
@dataclass(frozen=True, slots=True)
class QemuIdentity:
    user: str
    group: str
    uid: int
    gid: int

    @property
    def privileged(self) -> bool:
        return self.uid == 0


def _lookup_user(token: str) -> tuple[str, int] | None:
    token = token.strip().strip('"')
    try:
        if token.startswith("+") and token[1:].isdigit():
            entry = pwd.getpwuid(int(token[1:]))
        elif token.isdigit():
            entry = pwd.getpwuid(int(token))
        else:
            entry = pwd.getpwnam(token)
    except (KeyError, ValueError):
        return None
    return entry.pw_name, entry.pw_uid


def _lookup_group(token: str) -> tuple[str, int] | None:
    token = token.strip().strip('"')
    try:
        if token.startswith("+") and token[1:].isdigit():
            entry = grp.getgrgid(int(token[1:]))
        elif token.isdigit():
            entry = grp.getgrgid(int(token))
        else:
            entry = grp.getgrnam(token)
    except (KeyError, ValueError):
        return None
    return entry.gr_name, entry.gr_gid


def resolve_qemu_identity() -> QemuIdentity:
    """
    Honor explicit qemu.conf overrides and discover omitted defaults from the
    system libvirt daemon's DAC capabilities. Commented examples are not defaults.
    """
    user_tok = group_tok = None
    if QEMU_CONF.is_file():
        for raw in QEMU_CONF.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            match = re.match(r'^(user|group)\s*=\s*"?([^"#\s]+)"?', line)
            if match:
                if match.group(1) == "user":
                    user_tok = match.group(2)
                else:
                    group_tok = match.group(2)
    resolved_user = _lookup_user(user_tok) if user_tok else None
    resolved_group = _lookup_group(group_tok) if group_tok else None
    if user_tok and resolved_user is None:
        bail(f"qemu.conf sets user = {user_tok!r} but that account does not exist.")
    if group_tok and resolved_group is None:
        bail(f"qemu.conf sets group = {group_tok!r} but that group does not exist.")
    if resolved_user is None or resolved_group is None:
        result = run(["virsh", "-c", "qemu:///system", "capabilities"], timeout=30)
        if not result.ok:
            bail("Cannot discover QEMU's default account. Configure/start the modular daemons "
                 "with 10_virt_modular_daemon.py first. " + (result.err or result.out))
        try:
            label = ET.fromstring(result.out).findtext("./host/secmodel[model='dac']/baselabel[@type='kvm']", "")
        except ET.ParseError as exc:
            bail(f"Invalid libvirt capabilities XML: {exc}")
        match = re.fullmatch(r'\+?(\d+):\+?(\d+)', label.strip())
        if not match:
            bail("Libvirt did not report a usable KVM DAC account; set explicit user/group in qemu.conf.")
        resolved_user = resolved_user or _lookup_user(match[1])
        resolved_group = resolved_group or _lookup_group(match[2])
        if resolved_user is None or resolved_group is None:
            bail("Libvirt's QEMU UID/GID does not resolve to installed accounts.")
    name, uid = resolved_user
    gname, gid = resolved_group
    return QemuIdentity(name, gname, uid, gid)


# ==============================================================================
# FILESYSTEM FACTS
# ==============================================================================
@dataclass(frozen=True, slots=True)
class MountFacts:
    mountpoint: str
    fstype: str
    options: str
    source: str

    @property
    def volatile(self) -> bool:
        return self.fstype in {"tmpfs", "ramfs"} or self.source.startswith("/dev/zram")


def mount_facts(path: Path) -> MountFacts:
    """Longest-prefix match against /proc/self/mountinfo (no external tooling)."""
    best: MountFacts | None = None
    best_len = -1
    target = str(path.resolve())
    for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
        try:
            left, right = line.split(" - ", 1)
            fields = left.split()
            mountpoint = fields[4].replace("\\040", " ")
            opts = fields[5]
            rfields = right.split()
            fstype, source = rfields[0], rfields[1]
        except (ValueError, IndexError):
            continue
        if target == mountpoint or target.startswith(mountpoint.rstrip("/") + "/"):
            if len(mountpoint) > best_len:
                best_len = len(mountpoint)
                best = MountFacts(mountpoint, fstype, opts, source)
    return best or MountFacts("/", "unknown", "", "unknown")


# ==============================================================================
# POSIX.1e ACL PLANE
# ==============================================================================
PERM_BITS = {"r": 4, "w": 2, "x": 1}


def perm_value(triplet: str) -> int:
    return sum(PERM_BITS.get(ch, 0) for ch in triplet)


def read_acl(path: Path) -> dict[str, str]:
    """
    Parse 'getfacl -cEp' into {qualified-key: perms}, e.g.
      'user:qemu' -> 'r-x',  'default:user:qemu' -> 'rwx'
    """
    res = run(["getfacl", "-cEp", "--", str(path)], timeout=30)
    if not res.ok:
        bail(f"getfacl failed on {path}: {res.err or res.out}")
    acl: dict[str, str] = {}
    for raw in res.out.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) == 4 and parts[0] == "default":
            acl[f"default:{parts[1]}:{parts[2]}"] = parts[3]
        elif len(parts) == 3:
            acl[f"{parts[0]}:{parts[1]}"] = parts[2]
    return acl


def acl_satisfied(path: Path, wanted: dict[str, str]) -> bool:
    current = read_acl(path)
    for key, need in wanted.items():
        have = current.get(key)
        if have is None:
            return False
        if perm_value(have) & perm_value(need) != perm_value(need):
            return False
    return True


def apply_acl(path: Path, specs: list[str], *, default: bool = False) -> None:
    argv = ["setfacl"]
    if default:
        argv.append("-d")
    argv += ["-m", ",".join(specs), "--", str(path)]
    res = run(argv, timeout=30)
    if not res.ok:
        detail = res.err or res.out
        if "Operation not supported" in detail:
            bail(
                f"The filesystem backing {path} does not implement POSIX.1e ACLs "
                f"({mount_facts(path).fstype}). Choose an ext4/xfs/btrfs/tmpfs target."
            )
        bail(f"setfacl failed on {path}: {detail}")


def provision_acls(target: Path, qemu: QemuIdentity, operator: pwd.struct_passwd) -> list[str]:
    """
    Traversal (--x) on every parent, full rwx + inheritable default on the target.
    Returns a human-readable ledger of what actually changed.
    """
    console.print("\n[bold blue]==>[/bold blue] [bold]Enforcing storage ACLs[/bold]")
    ledger: list[str] = []

    principals: list[tuple[str, str]] = [("u", operator.pw_name)]
    if not qemu.privileged:
        principals.append(("u", qemu.user))
    else:
        console.print(
            "[dim]  QEMU runs with UID 0 -- traversal ACLs are "
            "provisioned for the operator only.[/dim]"
        )

    traversal_want = {f"user:{name}": "r-x" for _, name in principals}
    for parent in reversed(target.parents):
        if str(parent) == "/":
            continue
        if not parent.exists():
            continue
        # If standard unix permissions already grant read and execute (e.g. 0755),
        # skip adding redundant ACLs that override base permissions.
        stat_mode = parent.stat().st_mode
        if (stat_mode & 0o005) == 0o005 and parent.stat().st_uid == 0:
            continue
        if acl_satisfied(parent, traversal_want):
            continue
        apply_acl(parent, [f"{kind}:{name}:rx" for kind, name in principals])
        ledger.append(f"+rx  {parent}")

    full_want: dict[str, str] = {}
    for _, name in principals:
        full_want[f"user:{name}"] = "rwx"
        full_want[f"default:user:{name}"] = "rwx"
    if not acl_satisfied(target, full_want):
        apply_acl(target, [f"{kind}:{name}:rwx" for kind, name in principals])
        apply_acl(target, [f"{kind}:{name}:rwx" for kind, name in principals], default=True)
        ledger.append(f"rwx + default:rwx  {target}")

    if ledger:
        for entry in ledger:
            console.print(f"[green]  ~[/green] {entry}")
    else:
        console.print("[bold green]  ok[/bold green] ACL graph already convergent; no writes.")
    return ledger


# ==============================================================================
# TOPOLOGY SELECTION
# ==============================================================================
def choose_target(operator: pwd.struct_passwd, forced: str | None) -> tuple[Path, str]:
    if forced:
        path = Path(forced)
        if not path.is_absolute():
            bail("--path must be absolute.")
        return path, "custom"

    default_path = Path("/var/lib/libvirt/images")
    console.print("\n[bold cyan]Storage topology[/bold cyan]")
    console.print(f"  [1] Persistent   [dim]{default_path}[/dim]")
    console.print("  [2] Ephemeral    [dim]RAM-backed (zram/tmpfs), e.g. /mnt/zram1[/dim]")
    console.print("  [3] Custom       [dim]absolute path, TAB completion[/dim]")
    choice = Prompt.ask("Selection", choices=["1", "2", "3"], default="1")

    match choice:
        case "1":
            return default_path, "persistent"
        case "2":
            raw = Prompt.ask("Ephemeral mount path", default="/mnt/zram1")
            return Path(raw), "ephemeral"
        case _:
            with PathCompleter(Path(operator.pw_dir)) as completer:
                while True:
                    try:
                        raw = input("absolute path > ").strip().strip("\"'")
                    except EOFError:
                        raw = ""
                    candidate = Path(completer.expand(raw)) if raw else Path()
                    if candidate.is_absolute():
                        return candidate, "custom"
                    console.print("[red]  x Path must be absolute (start with '/').[/red]")


def prepare_directory(target: Path, mode_str: str) -> MountFacts:
    if not target.exists():
        console.print(f"[cyan]  Creating {target}[/cyan]")
        target.mkdir(parents=True, exist_ok=True, mode=0o711)
    elif not target.is_dir():
        bail(f"{target} exists and is not a directory.")

    facts = mount_facts(target)
    if mode_str == "ephemeral" and not facts.volatile:
        console.print(
            f"[yellow]  ! {target} is on {facts.fstype} ({facts.source}), which is NOT volatile. "
            "It will behave as persistent storage.[/yellow]"
        )
    if facts.volatile and mode_str != "ephemeral":
        console.print(
            f"[yellow]  ! {target} is RAM-backed ({facts.fstype}); every qcow2 there dies on "
            "reboot.[/yellow]"
        )
    if "noexec" in facts.options.split(","):
        console.print(f"[yellow]  ! {facts.mountpoint} is mounted noexec.[/yellow]")
    return facts


# ==============================================================================
# MAIN
# ==============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="Arsonix Phase 1.5: storage + ACL")
    parser.add_argument("--path", help="non-interactive absolute target directory")
    parser.add_argument("--yes", action="store_true", help="assume yes for volatile confirmation")
    args = parser.parse_args()
    elevate()

    console.clear()
    console.print(
        Panel(
            "[bold green]Arsonix Phase 1.5 -- Storage & ACL Provisioning[/bold green]\n"
            "POSIX.1e traversal / inheritable defaults / durable state",
            expand=False,
            border_style="green",
        )
    )

    if shutil.which("setfacl") is None or shutil.which("getfacl") is None:
        bail("acl utilities missing. Install with:  pacman -S --needed acl")

    operator = resolve_operator()
    qemu = resolve_qemu_identity()
    console.print(
        f"[dim]operator = {operator.pw_name} | qemu identity = {qemu.user}:{qemu.group} "
        f"({qemu.uid}:{qemu.gid})[/dim]"
    )

    target, mode_str = choose_target(operator, args.path)
    facts = prepare_directory(target, mode_str)

    if facts.volatile and not args.yes:
        if not Confirm.ask(
            f"[yellow]{target} is volatile ({facts.fstype}). Continue?[/yellow]", default=True
        ):
            console.print("[yellow]Aborted by operator.[/yellow]")
            raise SystemExit(0)

    provision_acls(target, qemu, operator)

    state_merge(
        storage_dir=str(target),
        storage_mode=mode_str,
        storage_fstype=facts.fstype,
        storage_volatile=facts.volatile,
        qemu_user=qemu.user,
        qemu_group=qemu.group,
        human_user=operator.pw_name,
        phase_07="complete",
    )

    table = Table(title="Phase 1.5 -- storage result", header_style="bold magenta")
    table.add_column("Facet", style="cyan")
    table.add_column("Value")
    table.add_row("Target", str(target))
    table.add_row("Mode", mode_str)
    table.add_row("Backing", f"{facts.fstype} on {facts.source} @ {facts.mountpoint}")
    table.add_row("Volatile", "yes" if facts.volatile else "no")
    table.add_row("QEMU identity", f"{qemu.user}:{qemu.group}")
    table.add_row("State", str(STATE_FILE))
    console.print(table)

    console.print("\n[bold green]=== PHASE 1.5 COMPLETE ===[/bold green]")
    console.print("Storage is ready. Continue the selected profile's GPU/network setup.\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n\n[bold red]! Interrupted by operator.[/bold red]\n")
        raise SystemExit(130) from None
