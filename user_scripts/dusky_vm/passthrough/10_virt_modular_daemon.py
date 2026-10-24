#!/usr/bin/env python3
"""
Arsonix KVM/VFIO Pipeline -- Phase 2 (10_virt_modular_daemon.py)
libvirt 12.6+ modular daemon topology: monolith eradication, systemd socket activation,
Polkit-native RBAC, and on-demand zero-RSS idle hypervisor lifecycle.

Target : Arch Linux rolling (Aug 2026) / libvirt 12.6+ / systemd 261+ / Polkit 127+
Policy : Pure socket activation. Modular daemons (virtqemud, virtproxyd, virtnetworkd, etc.).
         Zero idle RAM. Dynamic Polkit authorization for libvirt/wheel groups.
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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Never

MIN_PY: tuple[int, int, int] = (3, 14, 7)
STATE_DIR = Path("/var/lib/arsonix")
STATE_FILE = STATE_DIR / "state.json"
STATE_SCHEMA = 2
DROPIN_ROOT = Path("/etc/systemd/system")
DROPIN_NAME = "10-arsonix.conf"
LIBVIRT_ETC = Path("/etc/libvirt")
RUNTIME_SOCK_DIR = Path("/run/libvirt")


def _hard_exit(msg: str) -> Never:
    sys.stderr.write(f"\n[FATAL] {msg}\n\n")
    raise SystemExit(1)


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


if sys.version_info[:3] < MIN_PY:
    _hard_exit("Python 3.14.7+ required.")
elevate()

try:
    from rich.console import Console
    from rich.panel import Panel
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


# ==============================================================================
# SYSTEMD INTROSPECTION
# ==============================================================================
def list_unit_files(pattern: str) -> dict[str, str]:
    """{unit: enablement-state} for units that actually exist on this host."""
    res = run(
        ["systemctl", "list-unit-files", "--no-legend", "--no-pager", "--plain", pattern],
        timeout=60,
    )
    units: dict[str, str] = {}
    for line in res.out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and "." in parts[0]:
            units[parts[0]] = parts[1]
    return units


def unit_props(unit: str, props: list[str]) -> dict[str, str]:
    res = run(["systemctl", "show", unit, "--property=" + ",".join(props), "--no-pager"], timeout=30)
    out: dict[str, str] = {}
    for line in res.out.splitlines():
        key, _, val = line.partition("=")
        out[key] = val
    return {key: out.get(key, "") for key in props}


def systemctl(action: str, units: list[str], *, tolerate: bool = False) -> None:
    if not units:
        return
    res = run(["systemctl", action, *units], timeout=180)
    if not res.ok and not tolerate:
        bail(f"systemctl {action} failed:\n{res.err or res.out}")


# ==============================================================================
# STAGE 1 -- eradicate the monolith
# ==============================================================================
def eradicate_monolith() -> list[str]:
    console.print("\n[bold blue]==>[/bold blue] [bold]Eradicating the monolithic libvirtd[/bold]")
    legacy: dict[str, str] = {}
    for pattern in ("libvirtd.service", "libvirtd*.socket"):
        legacy |= list_unit_files(pattern)
    if not legacy:
        console.print("[bold green]  ok[/bold green] No monolithic units on this host.")
        return []

    # Never mask a unit that something else hard-Requires: that turns a soft
    # deprecation into a boot-time failure.
    guests = unit_props("libvirt-guests.service", ["Requires", "Requisite", "BindsTo"])
    hard_deps = " ".join(guests.values()).split()

    already = [u for u, s in legacy.items() if s == "masked"]
    todo = [u for u, s in legacy.items() if s != "masked"]
    protected = [u for u in todo if u in hard_deps]
    maskable = [u for u in todo if u not in hard_deps]

    systemctl("stop", list(legacy), tolerate=True)
    systemctl("disable", todo, tolerate=True)
    systemctl("mask", maskable, tolerate=True)
    run(["systemctl", "daemon-reload"], check=True, timeout=120)

    for unit in sorted(already):
        console.print(f"[dim]  = {unit} already masked[/dim]")
    for unit in sorted(maskable):
        console.print(f"[green]  ~[/green] {unit} stopped, disabled, masked")
    for unit in sorted(protected):
        console.print(
            f"[yellow]  ! {unit} left unmasked: hard-required by libvirt-guests.service[/yellow]"
        )
    return sorted(maskable)


# ==============================================================================
# STAGE 2 -- discover the modular fleet
# ==============================================================================
@dataclass(frozen=True, slots=True)
class Fleet:
    drivers: list[str]
    services: list[str]
    sockets_rw: list[str]
    sockets_ro: list[str]
    sockets_admin: list[str]
    sockets_net: list[str]


def discover_fleet() -> Fleet:
    all_services = sorted(list_unit_files("virt*d.service"))
    sockets = sorted(list_unit_files("virt*d*.socket"))
    rw, ro, admin, net = [], [], [], []
    for unit in sockets:
        stem = unit.removesuffix(".socket")
        if stem.endswith("-tcp") or stem.endswith("-tls"):
            net.append(unit)
        elif stem.endswith("-ro"):
            ro.append(unit)
        elif stem.endswith("-admin"):
            admin.append(unit)
        else:
            rw.append(unit)
    drivers = sorted({u.removesuffix(".service").removeprefix("virt").removesuffix("d")
                      for u in all_services})
    return Fleet(drivers, all_services, rw, ro, admin, net)


def enforce_socket_activation(fleet: Fleet) -> None:
    console.print("\n[bold blue]==>[/bold blue] [bold]Enforcing pure socket activation[/bold]")
    enabled = [u for u in fleet.services if list_unit_files(u).get(u) == "enabled"]
    systemctl("stop", fleet.services, tolerate=True)
    if enabled:
        systemctl("disable", enabled, tolerate=True)
        console.print(f"[green]  ~[/green] Disabled always-on services: {', '.join(enabled)}")
    else:
        console.print("[bold green]  ok[/bold green] No driver .service is enabled at boot.")
    if fleet.sockets_net:
        systemctl("stop", fleet.sockets_net, tolerate=True)
        systemctl("disable", fleet.sockets_net, tolerate=True)
        console.print(
            f"[green]  ~[/green] TCP/TLS listeners neutralised: {', '.join(fleet.sockets_net)}"
        )


# ==============================================================================
# STAGE 3 -- modern Polkit authorization & socket permission drop-ins
# ==============================================================================
DROPIN_RW = """# Managed by Arsonix (Phase 2). Do not edit.
# Modern libvirt 12.6+ / systemd socket activation with Polkit authorization.
[Socket]
SocketUser=root
SocketGroup=libvirt
SocketMode=0666
"""

DROPIN_RO = """# Managed by Arsonix (Phase 2). Do not edit.
[Socket]
SocketUser=root
SocketGroup=libvirt
SocketMode=0666
"""

DROPIN_ADMIN = """# Managed by Arsonix (Phase 2). Do not edit.
[Socket]
SocketUser=root
SocketGroup=root
SocketMode=0600
"""

POLKIT_DIR = Path("/etc/polkit-1/rules.d")
POLKIT_RULE_PATH = POLKIT_DIR / "50-arsonix-libvirt.rules"
POLKIT_RULE_PAYLOAD = """// Managed by Arsonix (Phase 2).
// Grants full unprivileged management permissions to members of libvirt and wheel groups
// without requiring interactive password prompts.
polkit.addRule(function(action, subject) {
    if (action.id.indexOf("org.libvirt.unix.") === 0 &&
        (subject.isInGroup("libvirt") || subject.isInGroup("wheel"))) {
        return polkit.Result.YES;
    }
});
"""


def install_polkit_rules() -> bool:
    console.print("\n[bold blue]==>[/bold blue] [bold]Installing Polkit authorization rules[/bold]")
    changed = False
    POLKIT_DIR.mkdir(parents=True, exist_ok=True, mode=0o755)
    if atomic_write(POLKIT_RULE_PATH, POLKIT_RULE_PAYLOAD, mode=0o644):
        changed = True
        console.print(f"[green]  ~[/green] {POLKIT_RULE_PATH}")
        console.print("[bold green]  ok[/bold green] Polkit rule written and armed.")
    else:
        console.print("[bold green]  ok[/bold green] Polkit rule already present.")
    return changed


def install_socket_dropins(fleet: Fleet) -> bool:
    console.print("\n[bold blue]==>[/bold blue] [bold]Installing socket permission drop-ins[/bold]")
    try:
        grp.getgrnam("libvirt")
    except KeyError:
        bail("Group 'libvirt' does not exist. The libvirt package did not install its sysusers.")

    changed = False
    for unit, payload in (
        [(u, DROPIN_RW) for u in fleet.sockets_rw]
        + [(u, DROPIN_RO) for u in fleet.sockets_ro]
        + [(u, DROPIN_ADMIN) for u in fleet.sockets_admin]
    ):
        path = DROPIN_ROOT / f"{unit}.d" / DROPIN_NAME
        if atomic_write(path, payload, mode=0o644):
            changed = True
            console.print(f"[green]  ~[/green] {path}")
    if changed:
        run(["systemctl", "daemon-reload"], check=True, timeout=120)
        console.print("[bold green]  ok[/bold green] Drop-ins written, systemd reloaded.")
    else:
        console.print("[bold green]  ok[/bold green] Drop-ins already convergent.")
    return changed


# ==============================================================================
# STAGE 4 -- daemon configuration files (honoured only when NOT socket-activated,
#            written anyway so a manual 'virtqemud' run behaves identically)
# ==============================================================================
def enforce_kv_config(path: Path, targets: dict[str, str], banner: str) -> bool:
    if not path.is_file():
        console.print(f"[yellow]  ! {path} absent; skipping.[/yellow]")
        return False
    original = path.read_text(encoding="utf-8")
    content = original
    for key, value in targets.items():
        pattern = re.compile(rf"^[ \t]*#?[ \t]*{re.escape(key)}[ \t]*=.*$", re.MULTILINE)
        line = f"{key} = {value}"
        if pattern.search(content):
            definitions = iter([line])
            content = pattern.sub(lambda _match: next(definitions, ""), content)
        else:
            if not content.endswith("\n"):
                content += "\n"
            content += f"\n# {banner}\n{line}\n"
    content = re.sub(r"\n{3,}", "\n\n", content)
    if atomic_write(path, content):
        console.print(f"[green]  ~[/green] {path} updated")
        return True
    console.print(f"[bold green]  ok[/bold green] {path} already correct")
    return False


def configure_daemon_conf() -> bool:
    console.print("\n[bold blue]==>[/bold blue] [bold]Daemon configuration[/bold]")
    touched = False
    targets = {
        "unix_sock_group": '"libvirt"',
        "unix_sock_rw_perms": '"0770"',
        "unix_sock_ro_perms": '"0777"',
        "auth_unix_rw": '"polkit"',
        "auth_unix_ro": '"none"',
    }
    for conf_path in sorted(LIBVIRT_ETC.glob("virt*d.conf")):
        touched |= enforce_kv_config(
            conf_path,
            targets,
            "Arsonix: modern socket activation & polkit authorization",
        )
    # libvirt >= 10.3 selects its packet-filter backend explicitly. nftables is the
    # only backend on a 2026 Arch host (iptables-nft shim is not installed).
    if (LIBVIRT_ETC / "network.conf").is_file():
        touched |= enforce_kv_config(
            LIBVIRT_ETC / "network.conf",
            {"firewall_backend": '"nftables"'},
            "Arsonix: nftables-native filtering (no iptables shim on this host)",
        )
    return touched


GUESTS_CONF = """# Managed by Arsonix (Phase 2).
URIS='qemu:///system'
ON_BOOT=start
ON_SHUTDOWN=shutdown
SHUTDOWN_TIMEOUT=120
PARALLEL_SHUTDOWN=4
START_DELAY=0
SYNC_TIME=1
"""


def configure_libvirt_guests() -> None:
    console.print("\n[bold blue]==>[/bold blue] [bold]Guest lifecycle on host shutdown[/bold]")
    conf = Path("/etc/conf.d/libvirt-guests")
    if atomic_write(conf, GUESTS_CONF, mode=0o644):
        console.print(f"[green]  ~[/green] {conf}")
    if not list_unit_files("libvirt-guests.service"):
        console.print("[yellow]  ! libvirt-guests.service not shipped; skipping.[/yellow]")
        return
    systemctl("enable", ["libvirt-guests.service"], tolerate=True)
    res = run(["systemctl", "start", "libvirt-guests.service"], timeout=120)
    if res.ok:
        console.print("[bold green]  ok[/bold green] libvirt-guests enabled and armed.")
    else:
        console.print(f"[yellow]  ! libvirt-guests could not start: {res.err or res.out}[/yellow]")


# ==============================================================================
# STAGE 5 -- arm the sockets
# ==============================================================================
def activate_sockets(fleet: Fleet, restart: bool) -> None:
    console.print("\n[bold blue]==>[/bold blue] [bold]Arming modular IPC sockets[/bold]")
    wanted = fleet.sockets_rw + fleet.sockets_ro + fleet.sockets_admin
    systemctl("enable", wanted, tolerate=True)
    if restart:
        # Config/drop-in changed: stop the running daemons so the next client
        # connection re-spawns them against the new socket + config.
        systemctl("stop", fleet.services, tolerate=True)
        systemctl("restart", wanted, tolerate=True)
    else:
        systemctl("start", wanted, tolerate=True)
    console.print(f"[bold green]  ok[/bold green] {len(wanted)} socket units enabled and listening.")


# ==============================================================================
# STAGE 6 -- empirical verification (facts on disk, not just unit states)
# ==============================================================================
def socket_facts(unit: str) -> str:
    props = unit_props(unit, ["Listen"])
    listen = props.get("Listen", "")
    match = re.search(r"(/[^ ]+) \(Stream\)", listen)
    if not match:
        return "[dim]-[/dim]"
    path = Path(match.group(1))
    try:
        st = path.stat()
    except OSError:
        return "[red]absent[/red]"
    try:
        group = grp.getgrgid(st.st_gid).gr_name
    except KeyError:
        group = str(st.st_gid)
    mode = stat.S_IMODE(st.st_mode)
    colour = "green" if mode in (0o660, 0o666, 0o600) else "yellow"
    return f"[{colour}]{group} {mode:04o}[/{colour}]"


def verification_table(fleet: Fleet) -> None:
    console.print("\n[bold blue]==>[/bold blue] [bold]Verification[/bold]")
    table = Table(title="libvirt 12.6+ modular topology", header_style="bold magenta")
    table.add_column("Driver", style="cyan")
    table.add_column(".socket", justify="center")
    table.add_column("sock group/mode", justify="center")
    table.add_column(".service", justify="center")

    for unit in fleet.sockets_rw:
        driver = unit.removesuffix(".socket")
        sock_state = unit_props(unit, ["ActiveState"])["ActiveState"]
        srv_state = unit_props(f"{driver}.service", ["ActiveState"])["ActiveState"]
        sock_fmt = (
            "[green]LISTENING[/green]" if sock_state == "active" else f"[red]{sock_state}[/red]"
        )
        srv_fmt = (
            "[blue]idle (0 RSS)[/blue]" if srv_state in ("inactive", "dead", "")
            else f"[yellow]{srv_state}[/yellow]"
        )
        table.add_row(driver, sock_fmt, socket_facts(unit), srv_fmt)
    console.print(table)

    legacy_state = unit_props("libvirtd.service", ["LoadState", "ActiveState", "UnitFileState"])
    if legacy_state["UnitFileState"] == "masked" or legacy_state["LoadState"] == "masked":
        console.print("[bold green]  ok[/bold green] libvirtd.service is masked.")
    elif legacy_state["LoadState"] == "not-found":
        console.print("[bold green]  ok[/bold green] libvirtd.service is not installed.")
    else:
        console.print(f"[yellow]  ! libvirtd.service state: {legacy_state}[/yellow]")


def connectivity_test() -> None:
    operator = state_load().get("human_user") or os.environ.get("SUDO_USER") or ""
    console.print("\n[bold blue]==>[/bold blue] [bold]Live IPC & Authorization test[/bold]")
    root_probe = run(["virsh", "-c", "qemu:///system", "-q", "version"], timeout=60)
    if root_probe.ok:
        console.print(f"[bold green]  ok[/bold green] root -> qemu:///system\n[dim]{root_probe.out}[/dim]")
    else:
        console.print(f"[red]  x root cannot reach qemu:///system: {root_probe.err}[/red]")

    if operator:
        try:
            pwd.getpwnam(operator)
        except KeyError:
            return
        user_probe = run(
            ["sudo", "-u", operator, "--", "virsh", "-c", "qemu:///system", "-q", "uri"], timeout=60
        )
        if user_probe.ok:
            console.print(f"[bold green]  ok[/bold green] {operator} -> {user_probe.out}")
        else:
            console.print(
                f"[yellow]  ! {operator} cannot reach qemu:///system:\n"
                f"    {user_probe.err or user_probe.out}[/yellow]"
            )
    console.print(
        "[dim]  virtqemud is socket-activated on demand; it self-terminates "
        "after its idle timeout, returning the host to 0 RSS.[/dim]"
    )


# ==============================================================================
# MAIN
# ==============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="Arsonix Phase 2: libvirt modular daemons")
    parser.add_argument("--no-guests", action="store_true", help="skip libvirt-guests")
    args = parser.parse_args()

    console.clear()
    console.print(
        Panel(
            "[bold green]Arsonix Phase 2 -- Modular Daemon & IPC Plane[/bold green]\n"
            "libvirt 12.6+ / systemd 261 socket activation / zero idle RSS / Polkit RBAC",
            expand=False,
            border_style="green",
        )
    )

    if shutil.which("virsh") is None:
        bail("virsh not found. Run Phase 1 first.")
    if not LIBVIRT_ETC.is_dir():
        bail("/etc/libvirt is absent. The libvirt package is not installed.")

    masked = eradicate_monolith()
    fleet = discover_fleet()
    if not fleet.sockets_rw:
        bail("No virt*d.socket units found: this libvirt build has no modular daemons.")
    console.print(f"[dim]discovered drivers: {', '.join(fleet.drivers)}[/dim]")

    enforce_socket_activation(fleet)
    polkit_changed = install_polkit_rules()
    dropins_changed = install_socket_dropins(fleet)
    conf_changed = configure_daemon_conf()
    activate_sockets(fleet, restart=dropins_changed or conf_changed or polkit_changed)
    if not args.no_guests:
        configure_libvirt_guests()

    verification_table(fleet)
    connectivity_test()

    state_merge(
        libvirt_drivers=fleet.drivers,
        libvirt_sockets=fleet.sockets_rw,
        libvirt_masked=masked,
        phase_10="complete",
    )

    console.print("\n[bold green]=== PHASE 2 COMPLETE ===[/bold green]")
    console.print("Next: 15_gpu_probing_kernal_param_mkinit.py (IOMMU topology + VFIO claim).\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n\n[bold red]! Interrupted by operator.[/bold red]\n")
        raise SystemExit(130) from None
