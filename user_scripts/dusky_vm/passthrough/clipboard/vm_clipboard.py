#!/usr/bin/env python3
"""Enable, inspect or stop supervised native Wayland clipboard sharing for a VM.

Host setup: vm_clipboard.py enable DOMAIN --ssh USER@HOST
Automated deployment: enable DOMAIN --from-json CONNECTION_JSON
No manual guest-side launch is needed. SSH authorization is set up once;
systemd reconnects after guest/desktop restarts. Source and keys stay on host.
"""

import argparse
import ipaddress
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

# Support both package imports and direct execution from any working directory.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from linux import deploy_vm as vm
from clipboard.wayland_clipboard import notify

PREFIX = "dusky-vm-clipboard@"


def config_root() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "dusky-vm/clipboard"


def domain_uuid(domain: str) -> str:
    return str(uuid.UUID(vm.virsh("domuuid", domain).stdout.strip()))


def folder(ident: str) -> Path:
    return config_root() / str(uuid.UUID(ident))


def write_config(path: Path, config: dict) -> None:
    # Config/key control data is small and persistent; transfer logs use /run.
    content = json.dumps(config, indent=2) + "\n"
    if path.exists() and path.read_text() == content:
        return
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".config-", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def command(config: dict) -> list[str]:
    directory = folder(config["uuid"])
    return ["ssh", "-i", str(directory / "key"), "-p", str(config["port"]),
            "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2",
            "-o", "StrictHostKeyChecking=yes", "-o", f"HostKeyAlias={config['uuid']}",
            "-o", f"UserKnownHostsFile={directory / 'known_hosts'}",
            f"{config['user']}@{config['address']}"]


def alias_host_keys(path: Path, ident: str) -> None:
    keys = []
    if path.exists():
        for line in path.read_text().splitlines():
            fields = line.split()
            if len(fields) >= 3 and not fields[0].startswith(("#", "@")):
                keys.append(f"{ident} {' '.join(fields[1:])}\n")
    content = "".join(dict.fromkeys(keys))
    if not path.exists() or path.read_text() != content:
        path.write_text(content)


def unit_text(script: Path) -> str:
    value = str(script).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
    return f'''[Unit]
Description=Wayland clipboard for VM %i
StartLimitIntervalSec=0

[Service]
Type=exec
ExecStart=/usr/bin/env python3 "{value}" run %i
Environment=PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
NotifyAccess=all
Restart=on-failure
RestartSec=5
TimeoutStopSec=15
RuntimeDirectory=dusky-vm-clipboard
RuntimeDirectoryMode=0700
RuntimeDirectoryPreserve=yes
StandardOutput=append:%t/dusky-vm-clipboard-%i.log
StandardError=inherit

[Install]
WantedBy=default.target
'''


def install_unit() -> None:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = config_home / "systemd/user" / f"{PREFIX}.service"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = unit_text(Path(__file__).resolve())
    if not path.exists() or path.read_text() != text:
        path.write_text(text)
        vm.run(["systemctl", "--user", "daemon-reload"])


def enable(domain: str, source: Path | None, target: str | None, port: int) -> str:
    ident = domain_uuid(domain)
    directory = folder(ident)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / "config.json"
    previous = json.loads(path.read_text()) if path.exists() else {}
    if source:
        original = json.loads(source.read_text())
        key = Path(original["assets"]) / "ssh-key"
        shutil.copyfile(key, directory / "key")
        shutil.copyfile(key.with_suffix(".pub"), directory / "key.pub")
        shutil.copyfile(Path(original["assets"]) / "known_hosts", directory / "known_hosts")
        alias_host_keys(directory / "known_hosts", ident)
        config = dict(uuid=ident, domain=domain, user=original["user"], address=original["address"],
                      port=22, discover_user=True)
    elif target:
        user, separator, address = target.rpartition("@")
        if not separator or not user or not address:
            raise RuntimeError("Use --ssh USER@HOST.")
        config = dict(uuid=ident, domain=domain, user=user, address=address, port=port, discover_user=False)
        if not (directory / "key").exists():
            vm.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"dusky-clipboard:{ident}",
                    "-f", str(directory / "key")])
        (directory / "known_hosts").touch(exist_ok=True)
        alias_host_keys(directory / "known_hosts", ident)
        if vm.run([*command(config), "true"], check=False).returncode:
            result = subprocess.run(["ssh-copy-id", "-i", str(directory / "key.pub"), "-p", str(port),
                                     "-o", "StrictHostKeyChecking=accept-new", "-o",
                                     f"HostKeyAlias={ident}", "-o",
                                     f"UserKnownHostsFile={directory / 'known_hosts'}", target], check=False)
            if result.returncode:
                raise RuntimeError("SSH key setup failed; no clipboard service enabled.")
    elif previous:
        config = previous
    else:
        raise RuntimeError("First enable requires --ssh USER@HOST or --from-json PATH.")
    alias_host_keys(directory / "known_hosts", ident)
    (directory / "key").chmod(0o600)
    config["boot_id"] = previous.get("boot_id", "")
    write_config(path, config)
    install_unit()
    unit = f"{PREFIX}{ident}.service"
    vm.run(["systemctl", "--user", "enable", unit])
    vm.run(["systemctl", "--user", "restart", unit])
    print(f"Clipboard auto-connect enabled for {domain}.")
    print(f"Status: {Path(__file__)} status {shlex.quote(domain)}")
    return ident


def dhcp_address(domain: str) -> str | None:
    root = ET.fromstring(vm.virsh("dumpxml", "--inactive", domain).stdout)
    networks = None
    for nic in root.findall("devices/interface"):
        source, mac = nic.find("source"), nic.find("mac")
        if source is None or mac is None:
            continue
        network = source.get("network")
        if not network and source.get("bridge"):
            if networks is None:
                networks = {}
                for name in vm.virsh("net-list", "--name").stdout.splitlines():
                    if not name:
                        continue
                    network_xml = ET.fromstring(vm.virsh("net-dumpxml", name).stdout)
                    bridge = network_xml.find("bridge")
                    if bridge is not None:
                        networks[bridge.get("name")] = name
            network = networks.get(source.get("bridge"))
        if network:
            leases = vm.virsh("net-dhcp-leases", network, "--mac", mac.get("address")).stdout
            for token in leases.split():
                if "/" not in token:
                    continue
                try:
                    address = ipaddress.ip_interface(token).ip
                except ValueError:
                    continue
                if address.version == 4:
                    return str(address)
    return None


def refresh(config: dict) -> dict:
    directory = folder(config["uuid"])
    address = dhcp_address(config["uuid"])
    if address:
        config["address"] = address
    if config["discover_user"] and vm.agent(config["uuid"], "guest-ping", check=False) is not None:
        boot = vm.guest_exec(config["uuid"], "from pathlib import Path; print(Path('/proc/sys/kernel/random/boot_id').read_text().strip())").strip()
        if boot != config.get("boot_id"):
            public_key = (directory / "key.pub").read_text()
            info = json.loads(vm.guest_exec(config["uuid"], vm.bootstrap_script(public_key, None)))
            config["user"] = info["user"]
            config["boot_id"] = boot
            (directory / "known_hosts").write_text(f"{config['uuid']} {info['host_key']}\n")
    write_config(directory / "config.json", config)
    return config


def run_bridge(ident: str) -> int:
    config = json.loads((folder(ident) / "config.json").read_text())
    while config["uuid"] not in vm.virsh("list", "--uuid").stdout.splitlines():
        if vm.virsh("dominfo", config["uuid"], check=False).returncode:
            notify(f"VM {config['domain']} is no longer defined; clipboard service stopped")
            return 0
        notify(f"Waiting for VM {config['domain']} to start")
        # Block on libvirt lifecycle events, with a timeout to close the
        # check/subscribe race. No continual process polling while the VM is off.
        vm.virsh("event", "--domain", config["uuid"], "--event", "lifecycle", "--timeout", "60",
                 check=False, timeout=65)
    config = refresh(config)
    notify(f"Connecting to {config['user']}@{config['address']}")
    source = vm.CLIPBOARD.read_text()
    upload = (
        "import json,os,tempfile; from pathlib import Path; "
        f"p=Path(tempfile.gettempdir()) / ('dusky-clipboard-{ident}-'+str(os.getuid())+'.py'); "
        "f=tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=p.parent,delete=False); "
        f"f.write({source!r}); f.close(); os.chmod(f.name,0o644); os.replace(f.name,p); print(json.dumps(str(p)))"
    )
    uploaded = vm.run([*command(config), "python3", "-c", shlex.quote(upload)], check=False)
    if uploaded.returncode:
        raise RuntimeError(f"SSH clipboard endpoint setup failed: {uploaded.stderr.strip()}")
    remote = json.loads(uploaded.stdout)
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "dusky-vm-clipboard"
    runtime.mkdir(mode=0o700, exist_ok=True)
    argv = [sys.executable, str(vm.CLIPBOARD), "--lock", str(runtime / f"{ident}.lock"), "--",
            *command(config), "python3", shlex.quote(remote), "--remote"]
    child = subprocess.Popen(argv)
    try:
        def stop(_signum, _frame):
            child.terminate()
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, stop)
        child.wait()
        notify("Clipboard connection ended; reconnecting")
        return 1
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=10)


def status(domain: str) -> None:
    ident = domain_uuid(domain)
    result = vm.run(["systemctl", "--user", "show", f"{PREFIX}{ident}.service",
                     "--property=ActiveState,SubState,StatusText,NRestarts", "--no-pager"], check=False)
    print(result.stdout.strip())
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    print(f"Log: {runtime / ('dusky-vm-clipboard-' + ident + '.log')}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    sub = parser.add_subparsers(dest="action", required=True)
    enable_parser = sub.add_parser("enable")
    enable_parser.add_argument("domain")
    setup = enable_parser.add_mutually_exclusive_group()
    setup.add_argument("--ssh", metavar="USER@HOST")
    setup.add_argument("--from-json", type=Path)
    enable_parser.add_argument("--port", type=int, default=22)
    for name in ("status", "disable", "restart"):
        sub.add_parser(name).add_argument("domain")
    sub.add_parser("run").add_argument("uuid", type=lambda s: str(uuid.UUID(s)))
    args = parser.parse_args()
    try:
        if sys.version_info < (3, 15):
            raise RuntimeError("The host clipboard manager requires Python 3.15+.")
        if os.geteuid() == 0:
            raise RuntimeError("Run this command in your normal host user session, without sudo.")
        if args.action == "enable":
            enable(args.domain, args.from_json, args.ssh, args.port)
        elif args.action == "run":
            return run_bridge(args.uuid)
        elif args.action == "status":
            status(args.domain)
        else:
            unit = f"{PREFIX}{domain_uuid(args.domain)}.service"
            operation = ["disable", "--now", unit] if args.action == "disable" else ["restart", unit]
            vm.run(["systemctl", "--user", *operation])
        return 0
    except (OSError, RuntimeError, ValueError, KeyError, ET.ParseError, subprocess.SubprocessError) as exc:
        notify(f"Clipboard reconnect pending: {exc}")
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
