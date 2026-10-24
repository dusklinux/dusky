#!/usr/bin/env python3
"""
Arsonix KVM/VFIO Pipeline -- Phase 4 (20_networking_nmcli.py)
Adaptive guest networking: Layer-2 system bridge (br0) or Layer-3 NAT (virbr0).

Target : Arch Linux rolling (Aug 2026) / NetworkManager 1.58+ / libvirt 12.6+ / nftables
Policy : Verify reachability, not exit codes. Roll back on loss of the default route.
         Never blind-purge a libvirt network the operator may have customised.
"""

import argparse
import ipaddress
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Never

MIN_PY: tuple[int, int, int] = (3, 14, 7)
STATE_DIR = Path("/var/lib/arsonix")
STATE_FILE = STATE_DIR / "state.json"
STATE_SCHEMA = 2
BRIDGE = "br0"
NAT_NET = "default"
BRIDGE_NET = "host-bridge"


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
    from rich.prompt import Confirm
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

    @property
    def timed_out(self) -> bool:
        return self.code == 124


def run(argv: list[str], *, check: bool = False, timeout: float = 60.0) -> Cmd:
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
# nmcli TERSE PARSING (values are ':'-separated with '\' escapes -- naive split
# corrupts any connection name, SSID or IPv6 address containing a colon)
# ==============================================================================
def unescape_terse(line: str) -> list[str]:
    values: list[str] = []
    buf: list[str] = []
    index = 0
    while index < len(line):
        char = line[index]
        if char == "\\" and index + 1 < len(line):
            buf.append(line[index + 1])
            index += 2
            continue
        if char == ":":
            values.append("".join(buf))
            buf.clear()
            index += 1
            continue
        buf.append(char)
        index += 1
    values.append("".join(buf))
    return values


def nmcli_rows(fields: list[str], args: list[str], *, timeout: float = 60.0) -> list[dict[str, str]]:
    res = run(["nmcli", "-t", "-f", ",".join(fields), *args], timeout=timeout)
    if not res.ok:
        return []
    rows: list[dict[str, str]] = []
    for line in res.out.splitlines():
        if not line:
            continue
        values = unescape_terse(line)
        rows.append({key: values[i] if i < len(values) else "" for i, key in enumerate(fields)})
    return rows


def connection_property(name: str, prop: str) -> str:
    res = run(["nmcli", "-t", "-f", prop, "connection", "show", name], timeout=60)
    if not res.ok:
        return ""
    for line in res.out.splitlines():
        key, _, val = line.partition(":")
        if key == prop:
            return val
    return ""


# ==============================================================================
# HOST NETWORK FACTS
# ==============================================================================
@dataclass(frozen=True, slots=True)
class Uplink:
    iface: str
    wireless: bool
    profile: str
    profile_uuid: str
    ip4_method: str
    ip4_addresses: str
    ip4_gateway: str
    ip4_dns: str


def default_route_iface() -> str | None:
    res = run(["ip", "-j", "route", "show", "default"], timeout=30)
    if not res.ok or not res.out:
        return None
    try:
        routes = json.loads(res.out)
    except json.JSONDecodeError:
        return None
    routes = [r for r in routes if r.get("dev")]
    if not routes:
        return None
    routes.sort(key=lambda r: int(r.get("metric") or 0))
    return str(routes[0]["dev"])


def is_wireless(iface: str) -> bool:
    base = Path("/sys/class/net") / iface
    return (base / "wireless").exists() or (base / "phy80211").exists()


def probe_uplink() -> Uplink:
    iface = default_route_iface()
    if iface is None:
        bail(
            "No IPv4 default route. Bring the host online first "
            "(nmcli device status / nmcli device connect <iface>)."
        )
    if iface == BRIDGE:
        console.print(f"[dim]Default route already traverses {BRIDGE}.[/dim]")
    profile = profile_uuid = ""
    for row in nmcli_rows(["NAME", "UUID", "TYPE", "DEVICE"], ["connection", "show", "--active"]):
        if row["DEVICE"] == iface:
            profile, profile_uuid = row["NAME"], row["UUID"]
            break
    return Uplink(
        iface=iface,
        wireless=is_wireless(iface),
        profile=profile,
        profile_uuid=profile_uuid,
        ip4_method=connection_property(profile, "ipv4.method") if profile else "",
        ip4_addresses=connection_property(profile, "ipv4.addresses") if profile else "",
        ip4_gateway=connection_property(profile, "ipv4.gateway") if profile else "",
        ip4_dns=connection_property(profile, "ipv4.dns") if profile else "",
    )


def wait_for_default_route(expect_dev: str, seconds: float = 25.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if default_route_iface() == expect_dev:
            return True
        time.sleep(1.0)
    return False


# ==============================================================================
# LIBVIRT NETWORK OBJECTS
# ==============================================================================
def virsh(args: list[str], *, timeout: float = 60.0) -> Cmd:
    return run(["virsh", "-c", "qemu:///system", *args], timeout=timeout)


def require_network_driver() -> None:
    state = run(["systemctl", "is-active", "virtnetworkd.socket"], timeout=30).out
    if state != "active":
        bail(
            "virtnetworkd.socket is not active. Run Phase 2 (10_virt_modular_daemon.py) first: "
            "the network driver cannot be reached over qemu:///system without it."
        )


def libvirt_networks() -> dict[str, tuple[bool, bool]]:
    """{name: (active, autostart)}"""
    out: dict[str, tuple[bool, bool]] = {}
    res = virsh(["net-list", "--all"])
    for line in res.out.splitlines()[2:]:
        parts = line.split()
        if len(parts) >= 3:
            out[parts[0]] = (parts[1] == "active", parts[2] == "yes")
    return out


def define_network(name: str, xml: str) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f"arsonix-{name}-", suffix=".xml")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(xml)
            handle.flush()
            os.fsync(handle.fileno())
        res = virsh(["net-define", str(tmp)])
        if not res.ok:
            bail(f"libvirt rejected the {name} network XML:\n{res.err or res.out}")
    finally:
        tmp.unlink(missing_ok=True)


def ensure_network_running(name: str) -> None:
    nets = libvirt_networks()
    active, autostart = nets.get(name, (False, False))
    if not active:
        res = virsh(["net-start", name])
        if not res.ok:
            console.print(f"[yellow]  ! net-start {name}: {res.err or res.out}[/yellow]")
    if not autostart:
        virsh(["net-autostart", name])
    console.print(f"[bold green]  ok[/bold green] libvirt network '{name}' active + autostart.")


NAT_XML = """<network>
  <name>default</name>
  <forward mode='nat'>
    <nat>
      <port start='1024' end='65535'/>
    </nat>
  </forward>
  <bridge name='virbr0' stp='on' delay='0'/>
  <ip address='192.168.122.1' netmask='255.255.255.0'>
    <dhcp>
      <range start='192.168.122.2' end='192.168.122.254'/>
    </dhcp>
  </ip>
</network>
"""

BRIDGE_XML = """<network>
  <name>host-bridge</name>
  <forward mode='bridge'/>
  <bridge name='br0'/>
</network>
"""


def host_owns_nat_subnet() -> bool:
    res = run(["ip", "-j", "route", "show"], timeout=30)
    if not res.ok:
        return False
    try:
        routes = json.loads(res.out)
    except json.JSONDecodeError:
        return False
    target = ipaddress.ip_network("192.168.122.0/24")
    for route in routes:
        dst = route.get("dst", "")
        if dst in ("default", ""):
            continue
        try:
            net = ipaddress.ip_network(dst, strict=False)
        except ValueError:
            continue
        if net.version == 4 and net.overlaps(target) and route.get("dev") != "virbr0":
            return True
    return False


def provision_nat(purge: bool) -> str:
    console.print("\n[bold cyan]--- Layer 3 NAT (virbr0) ---[/bold cyan]")
    if shutil.which("dnsmasq") is None:
        bail("dnsmasq is not installed; libvirt cannot run a NAT network without it.")
    if host_owns_nat_subnet():
        console.print(
            "[yellow]  ! 192.168.122.0/24 is already routed on this host by another device; "
            "guest traffic may be misrouted.[/yellow]"
        )
    nets = libvirt_networks()
    if NAT_NET in nets and not purge:
        console.print(
            "[green]  = 'default' already defined; preserving its DHCP/IP customisation.[/green]"
        )
        ensure_network_running(NAT_NET)
        return "nat-preserved"
    if NAT_NET in nets:
        virsh(["net-destroy", NAT_NET])
        virsh(["net-undefine", NAT_NET])
        console.print("[yellow]  ~ purged existing 'default' network[/yellow]")
    define_network(NAT_NET, NAT_XML)
    ensure_network_running(NAT_NET)
    return "nat-pristine"


# ==============================================================================
# LAYER 2 BRIDGE
# ==============================================================================
def bridge_exists() -> bool:
    return any(
        row["NAME"] == BRIDGE and row["TYPE"] == "bridge"
        for row in nmcli_rows(["NAME", "TYPE"], ["connection", "show"])
    )


def rollback_bridge(uplink: Uplink, port_name: str) -> None:
    console.print("\n[bold red]! Bridge activation failed. Surgical rollback...[/bold red]")
    run(["nmcli", "connection", "down", BRIDGE], timeout=30)
    run(["nmcli", "connection", "delete", BRIDGE], timeout=30)
    run(["nmcli", "connection", "delete", port_name], timeout=30)
    if uplink.profile:
        run(["nmcli", "connection", "modify", uplink.profile, "connection.autoconnect", "yes"],
            timeout=30)
        run(["nmcli", "--wait", "20", "connection", "up", uplink.profile], timeout=40)
    else:
        run(["nmcli", "device", "connect", uplink.iface], timeout=40)
    if wait_for_default_route(uplink.iface, 25):
        console.print("[bold green]  ok[/bold green] Physical uplink restored.")
    else:
        console.print(
            Panel(
                f"The default route did not return on {uplink.iface}. Recover manually:\n"
                f"  nmcli device connect {uplink.iface}\n"
                "  systemctl restart NetworkManager",
                title="MANUAL INTERVENTION",
                border_style="red",
            )
        )


def provision_bridge(uplink: Uplink) -> str:
    console.print("\n[bold cyan]--- Layer 2 system bridge (br0) ---[/bold cyan]")
    port_name = f"{BRIDGE}-port-{uplink.iface}"

    if bridge_exists():
        console.print(f"[green]  = '{BRIDGE}' profile already exists; not rebuilding.[/green]")
    else:
        console.print(f"[cyan]  Building {BRIDGE} over {uplink.iface} "
                      f"(profile '{uplink.profile or 'n/a'}')[/cyan]")
        run(
            ["nmcli", "connection", "add", "type", "bridge", "ifname", BRIDGE, "con-name", BRIDGE,
             "bridge.stp", "no", "connection.autoconnect", "yes",
             "ipv6.method", "auto"],
            check=True, timeout=60,
        )
        # Carry a static uplink configuration onto the bridge; otherwise DHCP.
        if uplink.ip4_method == "manual" and uplink.ip4_addresses:
            console.print("[cyan]  Migrating static IPv4 configuration to the bridge[/cyan]")
            args = ["nmcli", "connection", "modify", BRIDGE,
                    "ipv4.method", "manual", "ipv4.addresses", uplink.ip4_addresses]
            if uplink.ip4_gateway:
                args += ["ipv4.gateway", uplink.ip4_gateway]
            if uplink.ip4_dns:
                args += ["ipv4.dns", uplink.ip4_dns]
            run(args, check=True, timeout=60)
        else:
            run(["nmcli", "connection", "modify", BRIDGE, "ipv4.method", "auto"],
                check=True, timeout=60)

        run(
            ["nmcli", "connection", "add", "type", "ethernet", "ifname", uplink.iface,
             "con-name", port_name, "controller", BRIDGE, "port-type", "bridge",
             "connection.autoconnect", "yes"],
            check=True, timeout=60,
        )
        if uplink.profile:
            # Stop the old profile from racing the port for the same interface.
            run(["nmcli", "connection", "modify", uplink.profile,
                 "connection.autoconnect", "no"], timeout=30)
            run(["nmcli", "connection", "down", uplink.profile], timeout=40)

        console.print("[cyan]  Activating (20s budget)...[/cyan]")
        activation = run(["nmcli", "--wait", "20", "connection", "up", BRIDGE], timeout=45)
        reachable = activation.ok and wait_for_default_route(BRIDGE, 25)
        if not reachable:
            reason = "timeout" if activation.timed_out else (activation.err or "no default route")
            console.print(f"[red]  x {reason}[/red]")
            rollback_bridge(uplink, port_name)
            console.print("[yellow]Falling back to isolated NAT topology.[/yellow]")
            return provision_nat(purge=False)
        console.print(f"[bold green]  ok[/bold green] {BRIDGE} is up and carries the default route.")

    nets = libvirt_networks()
    if BRIDGE_NET not in nets:
        define_network(BRIDGE_NET, BRIDGE_XML)
    ensure_network_running(BRIDGE_NET)
    return "bridge"


# ==============================================================================
# FIREWALL
# ==============================================================================
def configure_firewall(device: str) -> str:
    console.print("\n[bold cyan]--- Packet filter ---[/bold cyan]")
    if shutil.which("nft") is not None:
        console.print("[dim]  nftables present; libvirt manages its own 'libvirt_network' table.[/dim]")
    ufw = shutil.which("ufw")
    if ufw is None:
        return "nftables (libvirt-managed)"
    status = run([ufw, "status"], timeout=30).out
    if not status.startswith("Status: active"):
        console.print("[dim]  ufw installed but inactive; no rules injected.[/dim]")
        return "nftables (ufw inactive)"
    for direction in ("in", "out"):
        run([ufw, "route", "allow", direction, "on", device], timeout=30)
    run([ufw, "reload"], timeout=60)
    console.print(f"[bold green]  ok[/bold green] ufw route allow in/out on {device}.")
    return f"ufw route allow on {device}"


# ==============================================================================
# MAIN
# ==============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="Arsonix Phase 4: guest networking")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--bridge", action="store_true", help="force Layer 2 bridge")
    mode.add_argument("--nat", action="store_true", help="force Layer 3 NAT")
    parser.add_argument("--purge-default", action="store_true",
                        help="re-provision libvirt 'default' from scratch")
    args = parser.parse_args()

    console.print(
        Panel("[bold white]Arsonix Phase 4 -- Adaptive Guest Networking[/bold white]",
              border_style="cyan")
    )

    for binary in ("ip", "nmcli", "virsh", "systemctl"):
        if shutil.which(binary) is None:
            bail(f"Missing mandatory binary: {binary}")
    if run(["systemctl", "is-active", "NetworkManager.service"], timeout=30).out != "active":
        bail("NetworkManager is not active; this phase drives nmcli exclusively.")
    require_network_driver()

    uplink = probe_uplink()
    console.print(
        f"[*] Uplink: [bold yellow]{uplink.iface}[/bold yellow] "
        f"({'802.11 wireless' if uplink.wireless else 'wired'}) "
        f"profile='{uplink.profile}' ipv4.method={uplink.ip4_method or 'n/a'}"
    )

    if args.nat:
        topology = provision_nat(purge=args.purge_default)
    elif args.bridge:
        topology = provision_bridge(uplink)
    elif uplink.wireless:
        console.print(
            Panel(
                "802.11 STA interfaces cannot carry a Layer-2 bridge: the 3-address frame "
                "format has no field for the guest MAC, so the AP drops the traffic. Only "
                "4-address (WDS/AP) mode works, and the AP must support it.",
                title="Hardware limitation",
                border_style="yellow",
            )
        )
        if Confirm.ask("Force bridging anyway (requires WDS)?", default=False):
            topology = provision_bridge(uplink)
        else:
            topology = provision_nat(purge=args.purge_default)
    else:
        if Confirm.ask(f"Provision a Layer 2 bridge on {uplink.iface}?", default=True):
            topology = provision_bridge(uplink)
        else:
            topology = provision_nat(purge=args.purge_default)

    device = BRIDGE if topology == "bridge" else "virbr0"
    firewall = configure_firewall(device)
    libvirt_net = BRIDGE_NET if topology == "bridge" else NAT_NET

    state_merge(
        network_topology=topology,
        network_device=device,
        network_uplink=uplink.iface,
        libvirt_network=libvirt_net,
        phase_20="complete",
    )

    table = Table(title="Phase 4 -- network architecture", header_style="bold magenta")
    table.add_column("Component", style="cyan")
    table.add_column("State")
    table.add_column("Detail")
    table.add_row("Uplink", "[green]discovered[/green]",
                  f"{uplink.iface} ({'wireless' if uplink.wireless else 'wired'})")
    table.add_row("Topology", "[cyan]provisioned[/cyan]", topology)
    table.add_row("Guest device", "[green]ready[/green]", device)
    table.add_row("Packet filter", "[green]applied[/green]", firewall)
    table.add_row("libvirt network", "[green]autostart[/green]", libvirt_net)
    table.add_row("Default route", "[green]verified[/green]", str(default_route_iface()))
    console.print(table)

    console.print("\n[bold green]=== PHASE 4 COMPLETE ===[/bold green]")
    console.print("Next: windows/25_looking_glass.py (shared-memory frame relay).\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n\n[bold red]! Interrupted by operator.[/bold red]\n")
        raise SystemExit(130) from None
