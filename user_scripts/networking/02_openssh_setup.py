#!/usr/bin/env python3
# ==============================================================================
# Arch Linux SSH Bootstrap v7.3
# ------------------------------------------------------------------------------
# Purpose: Auto-provision OpenSSH, configure firewalls, smart IP/Tailscale
#          detection, sshd.socket aware.
# Target:  Arch Linux (latest rolling), Wayland/Hyprland, Python 3.15+
# Usage:   02_openssh_setup.py [--auto]
# ==============================================================================

import argparse
import fcntl
import importlib
import json
import os
import pwd
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from ipaddress import ip_address
from pathlib import Path
from typing import Never

# --- 1. Early Privilege Escalation & Dependency Bootstrapping ---
def bootstrap_environment() -> None:
    """Elevate only after parsing CLI arguments, then load the presentation UI."""
    if os.geteuid() != 0:
        print("Root privileges required. Elevating via sudo...", flush=True)
        # The invoking process already handled confirmation. Carry that choice
        # into the elevated process so sudo does not cause a second prompt.
        os.execvp("sudo", [
            "sudo", sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], "--auto",
        ])
    if not Path("/etc/arch-release").is_file():
        raise RuntimeError("This script targets Arch Linux.")
    try:
        import rich
    except ModuleNotFoundError as exc:
        if exc.name != "rich":
            raise
        print("Installing python-rich via pacman...", flush=True)
        subprocess.run(["pacman", "-S", "--needed", "--noconfirm", "python-rich"], check=True)
        importlib.invalidate_caches()
        # Do not loop through exec: pacman's Python may differ from this interpreter.
        try:
            import rich
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                f"python-rich is unavailable to {sys.executable}; install it for this interpreter."
            ) from exc

    global console, Panel, Table, Text, Align
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.align import Align
    console = Console()

# --- Utility Functions ---


def run_cmd(cmd: list[str], check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
    """Run an argument vector and propagate command failures with their diagnostics."""
    try:
        return subprocess.run(
            cmd,
            check=check,
            capture_output=capture,
            text=True,
            env={**os.environ, "LC_ALL": "C"},
        )
    except FileNotFoundError:
        # Gracefully return a 127 (POSIX standard for 'command not found') if binary is missing
        if check:
            raise subprocess.CalledProcessError(127, cmd, stderr=f"Binary not found: {cmd[0]}")
        return subprocess.CompletedProcess(cmd, 127, stdout="", stderr=f"Binary not found: {cmd[0]}")


def log_info(msg: str) -> None:
    console.print(f"[bold blue]  ::[/] {msg}")


def log_success(msg: str) -> None:
    console.print(f"[bold green]  ✔[/] {msg}")


def log_warn(msg: str) -> None:
    console.print(f"[bold yellow]  ⚠[/] {msg}")


def log_error(msg: str) -> None:
    console.print(f"[bold red]  ✖[/] {msg}")


def die(msg: str, exc: Exception | None = None) -> Never:
    log_error(msg)
    if exc:
        console.print(f"    Details: {exc}", markup=False)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            console.print(exc.stderr.strip(), markup=False)
    sys.exit(1)

# --- Core Logic ---


def get_real_user() -> str:
    """Prefer sudo's invoking account; otherwise select a local user session."""
    if uid := os.environ.get("SUDO_UID"):
        return pwd.getpwuid(int(uid)).pw_name
    if shutil.which("loginctl"):
        out = run_cmd(["loginctl", "list-sessions", "--json=short"], check=False)
        if out.returncode == 0:
            for session in json.loads(out.stdout):
                if session.get("class") == "user" and session.get("seat"):
                    return pwd.getpwuid(int(session["uid"])).pw_name
    return pwd.getpwuid(os.getuid()).pw_name


def install_openssh() -> None:
    """Install the SSH daemon and QR encoder if missing."""
    missing = [pkg for pkg in ("openssh", "qrencode")
               if run_cmd(["pacman", "-Qq", pkg], check=False).returncode != 0]
    if not missing:
        log_success("OpenSSH and qrencode are already installed.")
        return

    with console.status(f"[bold cyan]Installing {', '.join(missing)} via pacman..."):
        try:
            run_cmd(["pacman", "-S", "--noconfirm", "--needed", *missing], check=True, capture=False)
            log_success("SSH packages installed successfully.")
        except subprocess.CalledProcessError as e:
            die("Installation failed. Run 'sudo pacman -Syu' first to sync repos.", e)


def generate_host_keys() -> None:
    """Ensure SSH host keys exist."""
    try:
        run_cmd(["ssh-keygen", "-A"])
        log_success("SSH host keys verified.")
    except subprocess.CalledProcessError as exc:
        die("SSH host key generation failed.", exc)


def validate_sshd_config() -> str:
    """Run built-in syntax check for sshd."""
    try:
        config = run_cmd(["sshd", "-T"]).stdout
        log_success("sshd configuration is valid.")
    except subprocess.CalledProcessError as e:
        die("sshd configuration is invalid. Fix /etc/ssh/sshd_config and re-run.", e)

    return config


def socket_tcp_listeners(unit: str) -> list[tuple[str, int]]:
    """Read systemd's merged TCP socket addresses, including drop-in resets."""
    listen = run_cmd(["systemctl", "show", unit, "--property=Listen", "--value"]).stdout
    listeners = []
    for endpoint in re.findall(r"(\S+) \(Stream\)", listen):
        if endpoint.startswith(("/", "@", "vsock:")):
            continue
        host, separator, value = endpoint.rpartition(":")
        if not separator:
            host, value = "::", endpoint
        value = value.split("%", 1)[0]
        if value.isdecimal():
            listeners.append((host.strip("[]"), int(value)))
    return listeners


def detect_unit_and_ports(config_text: str) -> tuple[str, str, list[int]]:
    """Select a running or explicitly enabled socket; otherwise use the service."""
    socket_selected = run_cmd(
        ["systemctl", "is-active", "--quiet", "sshd.socket"], check=False
    ).returncode == 0
    if not socket_selected:
        enabled = run_cmd(["systemctl", "is-enabled", "sshd.socket"], check=False)
        socket_selected = enabled.stdout.strip() in {"enabled", "enabled-runtime"}
    unit, unit_type = ("sshd.socket", "socket") if socket_selected else ("sshd.service", "service")
    if socket_selected:
        ports = [port for _, port in socket_tcp_listeners(unit)]
    else:
        # -T expands ListenAddress entries, including their explicit port overrides.
        addresses = [
            line.split()[1] for line in config_text.splitlines()
            if line.lower().startswith("listenaddress ")
        ]
        ports = [int(address.rsplit(":", 1)[-1]) for address in addresses]
    ports = list(dict.fromkeys(ports))
    if not ports or any(not 1 <= port <= 65535 for port in ports):
        die(f"Cannot determine valid TCP listening ports for {unit}.")
    log_info(f"Target SSH ports: {', '.join(map(str, ports))}")
    return unit, unit_type, ports


def analyze_security_warnings(config_text: str, user: str, unit_type: str) -> None:
    """Parse output of sshd -T to warn about common lockout issues from the base sshd configuration."""
    lines = [line.strip().lower() for line in config_text.splitlines()]

    listen_addrs = [line.split()[1] for line in lines if line.startswith("listenaddress ")]
    if listen_addrs and unit_type == "service":
        all_local = all(ip_address(addr.rsplit(":", 1)[0].strip("[]")).is_loopback for addr in listen_addrs)
        if all_local:
            log_warn("sshd listens ONLY on localhost. Remote connections will fail.")

    if user == "root":
        permit_root = next(
            (line.split()[1] for line in lines if line.startswith("permitrootlogin ")),
            "prohibit-password",
        )
        if permit_root == "no":
            log_warn("Base PermitRootLogin is 'no'; review any user-specific Match rules.")
        elif permit_root in ("prohibit-password", "without-password"):
            log_warn(f"PermitRootLogin is '{permit_root}'. Keys are required (no passwords).")

    pass_auth = next((line.split()[1] for line in lines if line.startswith("passwordauthentication ")), "yes")
    pubkey_auth = next((line.split()[1] for line in lines if line.startswith("pubkeyauthentication ")), "yes")
    kbd_auth = next((line.split()[1] for line in lines if line.startswith("kbdinteractiveauthentication ")), "no")
    host_auth = next((line.split()[1] for line in lines if line.startswith("hostbasedauthentication ")), "no")
    gssapi_auth = next((line.split()[1] for line in lines if line.startswith("gssapiauthentication ")), "no")

    if all(a == "no" for a in (pass_auth, pubkey_auth, kbd_auth, host_auth, gssapi_auth)):
        log_error(
            "[bold red]CRITICAL:[/] All configured authentication methods are disabled "
            "in the base configuration; review any Match rules."
        )

    if pass_auth == "no" and kbd_auth == "no":
        log_info("Password/interactive authentication is disabled; configure an accepted key or other enabled method.")


def check_port_conflicts(port: int) -> None:
    """Reject an occupied port before opening firewall rules or starting SSH."""
    out = run_cmd(["ss", "-Hltnp", f"sport = :{port}"]).stdout
    for line in out.splitlines():
        if '"sshd"' not in line and '"systemd"' not in line:
            die(f"Port {port} is already held by another process: {line}")


def persist_raw_ssh_rules(binaries: list[str], ports: list[int]) -> None:
    """Reapply just SSH allow rules at boot, without snapshotting live tables."""
    unit = "dusky-ssh-firewall.service"
    path = Path("/etc/systemd/system") / unit
    commands = []
    bash = shutil.which("bash")
    if bash is None:
        raise RuntimeError("Bash is required to restore raw SSH rules at boot.")
    for binary in binaries:
        for port in ports:
            rule = ["INPUT", "-p", "tcp", "--dport", str(port), "-j", "ACCEPT"]
            check = shlex.join([binary, "-w", "5", "-C", *rule])
            insert = shlex.join([binary, "-w", "5", "-I", "INPUT", "1", *rule[1:]])
            command = f'{check}; result=$?; if (( result == 1 )); then {insert}; else exit "$result"; fi'
            if Path(binary).name == "ip6tables":
                command = '[[ -e /proc/net/if_inet6 ]] || exit 0; ' + command
            # ':' prevents systemd from expanding Bash's variables. '%' still
            # needs escaping because systemd treats it as a unit specifier.
            commands.append(f"ExecStart=:{bash} -c {json.dumps(command).replace('%', '%%')}")
    content = "\n".join([
        "# Generated by 02_openssh_setup.py: allow SSH from any source.",
        "[Unit]",
        "Description=Restore permissive SSH firewall rules",
        "After=iptables.service ip6tables.service nftables.service",
        "Before=sshd.service",
        "",
        "[Service]",
        "Type=oneshot",
        "RemainAfterExit=yes",
        *commands,
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ])
    if not path.exists() or path.read_text(encoding="utf-8") != content:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, prefix=f".{unit}.", delete=False,
            ) as file:
                temporary = Path(file.name)
                file.write(content)
            temporary.chmod(0o644)
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        run_cmd(["systemctl", "daemon-reload"])
    run_cmd(["systemctl", "enable", unit])
    log_success("Raw SSH allow rules will be reapplied at boot, without replacing other firewall rules.")


def configure_firewalls(ports: list[int]) -> None:
    """Add idempotent rules and fail when an active manager rejects an update."""
    active_firewalls = 0
    if shutil.which("ufw"):
        status = run_cmd(["ufw", "status"]).stdout
        if "Status: active" in status:
            active_firewalls += 1
            for port in ports:
                run_cmd(["ufw", "allow", f"{port}/tcp"])
                log_success(f"UFW: Allowed {port}/tcp.")

    if shutil.which("firewall-cmd") and run_cmd(
        ["systemctl", "is-active", "--quiet", "firewalld"], check=False
    ).returncode == 0:
        active_firewalls += 1
        default = run_cmd(["firewall-cmd", "--get-default-zone"]).stdout.strip()
        active = run_cmd(["firewall-cmd", "--get-active-zones"]).stdout
        active_zones = [line for line in active.splitlines() if line and not line[0].isspace()]
        zones = list(dict.fromkeys([default, *active_zones]))
        for zone in zones:
            for port in ports:
                for options in ([], ["--permanent"]):
                    run_cmd(["firewall-cmd", *options, f"--zone={zone}", f"--add-port={port}/tcp"])
        log_success(f"Firewalld: SSH ports allowed in {', '.join(zones)} (runtime and permanent).")

    if active_firewalls == 0:
        binaries = [path for binary in ("iptables", "ip6tables") if (path := shutil.which(binary))]
        for binary in binaries:
            if Path(binary).name == "ip6tables" and not Path("/proc/net/if_inet6").exists():
                continue
            policy = run_cmd([binary, "-w", "5", "-S", "INPUT"])
            if "-P INPUT DROP" not in policy.stdout.splitlines():
                continue
            for port in ports:
                rule = ["INPUT", "-p", "tcp", "--dport", str(port), "-j", "ACCEPT"]
                exists = run_cmd([binary, "-w", "5", "-C", *rule], check=False)
                if exists.returncode == 1:
                    run_cmd([binary, "-w", "5", "-I", "INPUT", "1", *rule[1:]])
                elif exists.returncode != 0:
                    raise subprocess.CalledProcessError(exists.returncode, exists.args, stderr=exists.stderr)
            log_success(f"{Path(binary).name}: SSH ports allowed from any source.")
        if binaries:
            persist_raw_ssh_rules(binaries, ports)
        log_info("No active UFW/firewalld manager; other rulesets and remote reachability are unverified.")


def manage_services(unit: str, unit_type: str) -> None:
    """Activate SSH and apply validated configuration to an existing daemon."""
    was_active = run_cmd(["systemctl", "is-active", "--quiet", unit], check=False).returncode == 0
    run_cmd(["systemctl", "enable", "--now", unit])
    if was_active and unit_type == "service":
        run_cmd(["systemctl", "reload", unit])
    run_cmd(["systemctl", "is-active", "--quiet", unit])
    # With Accept=no, sshd.service may itself be triggered by sshd.socket.
    # Change its boot activation only after the socket successfully starts.
    if unit_type == "socket":
        enabled = run_cmd(["systemctl", "is-enabled", "sshd.service"], check=False)
        if enabled.stdout.strip() in {"enabled", "enabled-runtime"}:
            run_cmd(["systemctl", "disable", "sshd.service"])
    log_success(f"{unit} is enabled and active.")


def verify_listeners(ports: list[int], unit: str) -> dict[int, list[str]]:
    """Verify ownership and return actual bound addresses for connection guidance."""
    if unit.endswith(".socket"):
        owner = 1  # The system systemd instance holds socket-activation listeners.
        expected = socket_tcp_listeners(unit)
    else:
        owner = int(run_cmd(["systemctl", "show", unit, "--property=MainPID", "--value"]).stdout)
        if owner <= 0:
            die(f"{unit} has no running main process.")
        expected = None
    listeners = {}
    for port in ports:
        out = run_cmd(["ss", "-Hltnp", f"sport = :{port}"]).stdout
        addresses = []
        for line in out.splitlines():
            if f"pid={owner}," not in line:
                continue
            address = line.split()[3].rsplit(":", 1)[0].strip("[]")
            # ss renders a dual-stack wildcard as '*', an IPv6-only one as '[::]'.
            if expected is None or ("::" if address == "*" else address, port) in expected:
                addresses.append(address)
        if not addresses:
            die(f"No listener belonging to {unit} found on port {port}; check its live configuration.")
        listeners[port] = addresses
    return listeners


def select_connection(
    listeners: dict[int, list[str]], candidates: list[str | None]
) -> tuple[str, int] | None:
    """Prefer Tailscale, but advertise only an IPv4 address that SSH accepts."""
    for target in candidates:
        if not target:
            continue
        for port, addresses in listeners.items():
            if any(address in {"*", "0.0.0.0", target} for address in addresses):
                return target, port
    return None


def get_tailscale_ip() -> str | None:
    """Display Tailscale's address only when its IPv4 interface is actually up."""
    if not shutil.which("tailscale"):
        return None
    address = run_cmd(["tailscale", "ip", "-4"], check=False)
    if address.returncode != 0 or not address.stdout.strip():
        return None
    ip = address.stdout.strip()
    links = json.loads(run_cmd(["ip", "-j", "-4", "addr", "show", "up"]).stdout)
    if any(ip == addr.get("local") for link in links for addr in link.get("addr_info", [])):
        log_info(f"Tailscale address detected: {ip}")
        return ip
    return None


def trust_tailscale_interface(ip: str) -> None:
    """Restore broad Tailscale ingress independently of Tailscale setup."""
    links = json.loads(run_cmd(["ip", "-j", "-4", "addr", "show", "up"]).stdout)
    interface = next(
        (link["ifname"] for link in links if any(addr.get("local") == ip for addr in link.get("addr_info", []))),
        None,
    )
    if interface is None:
        raise RuntimeError("Tailscale interface disappeared before firewall configuration.")
    if shutil.which("firewall-cmd") and run_cmd(
        ["systemctl", "is-active", "--quiet", "firewalld"], check=False
    ).returncode == 0:
        for options in ([], ["--permanent"]):
            run_cmd(["firewall-cmd", *options, "--zone=trusted", f"--change-interface={interface}"])
        log_success(f"Firewalld: {interface} trusted (runtime and permanent).")
    if shutil.which("ufw") and "Status: active" in run_cmd(["ufw", "status"]).stdout:
        run_cmd(["ufw", "allow", "in", "on", interface])
        log_success(f"UFW: Incoming traffic allowed on {interface}.")


def get_lan_ip() -> str | None:
    """Prefer the default-route physical interface, without interface-name guesses."""
    data = json.loads(run_cmd(["ip", "-j", "-4", "addr", "show", "up", "scope", "global"]).stdout)
    routes = json.loads(run_cmd(["ip", "-j", "-4", "route", "show", "default"]).stdout)
    preferred = [route.get("dev") for route in sorted(routes, key=lambda route: route.get("metric", 0))]
    physical = [link for link in data if (Path("/sys/class/net") / link["ifname"] / "device").exists()]
    physical.sort(key=lambda link: preferred.index(link["ifname"]) if link["ifname"] in preferred else len(preferred))
    for link in physical:
        for addr in link.get("addr_info", []):
            if addr.get("family") == "inet" and addr.get("local"):
                return addr["local"]
    return None

# --- Application Entry Point ---


def main() -> None:
    parser = argparse.ArgumentParser(description="Arch Linux SSH Bootstrapper (Rich Edition)")
    parser.add_argument("-a", "--auto", action="store_true", help="Run non-interactively")
    args = parser.parse_args()
    if not args.auto:
        try:
            response = input("Enable SSH access to this machine? [Y/n] ")
        except EOFError:
            print("Cancelled (end of input).")
            return
        if response.casefold() not in {"", "y", "yes"}:
            print("Cancelled.")
            return
    bootstrap_environment()

    # Coordinate with Tailscale setup/teardown while changing shared networking.
    with Path("/run/lock/dusky-tailscale.lock").open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            die("Another networking setup/teardown is running.")

        user = get_real_user()

        # Initial Header
        console.print(Panel.fit(
            f"[bold white]Targeting Arch Linux / User:[/] [bold cyan]{user}[/]\n"
            "[dim]Provisions: OpenSSH · Firewalls · Tailscale · Systemd Sockets[/]",
            title="[bold green]Arch Linux SSH Provisioning v7.3[/]",
            border_style="blue",
            padding=(1, 4)
        ))

        # Pipeline
        install_openssh()
        generate_host_keys()

        config_text = validate_sshd_config()
        unit, unit_type, ports = detect_unit_and_ports(config_text)

        analyze_security_warnings(config_text, user, unit_type)
        for listening_port in ports:
            check_port_conflicts(listening_port)
        configure_firewalls(ports)
        manage_services(unit, unit_type)
        listeners = verify_listeners(ports, unit)

        # Networking (Fully Autonomous)
        ts_ip = get_tailscale_ip()
        if ts_ip:
            trust_tailscale_interface(ts_ip)
        lan_ip = get_lan_ip()

        # Final Output Table (Dual-IP Rendering)
        print()
        table = Table(title="[bold]SSH Setup Complete[/]", show_header=False, border_style="green", padding=(0, 2))
        table.add_column("Key", style="bold cyan", justify="right")
        table.add_column("Value", style="white")

        if ts_ip:
            table.add_row("Tailscale IP", f"[bold magenta]{ts_ip}[/]")
        if lan_ip:
            table.add_row("Local LAN IP", f"[bold green]{lan_ip}[/]")

        table.add_row("Ports", ", ".join(map(str, ports)))
        table.add_row("User", f"[bold green]{user}[/]")
        if unit_type == "socket":
            table.add_row("Activation", "[italic]socket (on-demand)[/]")

        console.print(table, justify="center")

        connection = select_connection(listeners, [ts_ip, lan_ip])
        if connection:
            target, port = connection
            conn_cmd = f"ssh {user}@{target}" if port == 22 else f"ssh -p {port} {user}@{target}"
            console.print(Panel(
                Text(conn_cmd, justify="center", style="bold magenta"),
                title="Connect from another device",
                border_style="magenta",
                width=60,
            ), justify="center")

            ssh_uri = f"ssh://{user}@{target}:{port}"
            # Keep the standard four-module quiet zone for scanner reliability.
            qr_out = run_cmd(["qrencode", "-t", "UTF8", "-m", "4", ssh_uri], check=False)
            if qr_out.returncode == 0 and qr_out.stdout.strip():
                qr_text = Text(qr_out.stdout, style="black on white")
                qr_width = max(map(len, qr_out.stdout.splitlines())) + 4
                if qr_width <= console.width:
                    console.print(Panel(
                        Align.center(qr_text),
                        title="[bold cyan]Scan to Connect[/]",
                        border_style="cyan",
                        width=max(min(60, console.width), qr_width),
                    ), justify="center")
                else:
                    log_info("Terminal too narrow for the QR code; use the connection command above.")
            else:
                log_warn("QR generation failed; use the connection command above.")
        else:
            log_warn(
                "No discovered IPv4 address matches the SSH listeners; "
                "check ListenAddress and interface configuration."
            )

        log_info(
            "Local activation and listening ports verified. "
            "Remote authentication and network policy require a client connection."
        )
        print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nSetup interrupted by user.", file=sys.stderr)
        sys.exit(130)
    except (ImportError, OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            print(exc.stderr.strip(), file=sys.stderr)
        sys.exit(1)
