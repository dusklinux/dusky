#!/usr/bin/env python3
"""Install/remove the opt-in Duskie clipboard user service (Python 3.15+)."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

UNIT = "dusky-phone-sync.service"
ROOT = Path(__file__).resolve().parent
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
CONFIG = CONFIG_HOME / "dusky/phone_sync.json"
SERVICE = CONFIG_HOME / "systemd/user" / UNIT


def ssh(config: dict, action: str, *, batch: bool = True) -> list[str]:
    return ["ssh", "-T", "-o", f'BatchMode={"yes" if batch else "no"}',
            "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=5",
            "-i", config["identity"], "-p", str(config["port"]),
            f'{config["user"]}@{config["host"]}',
            "/var/jb/usr/libexec/duskie-clipboard", action]


def quoted(value: str | Path) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'


def unit(config: Path = CONFIG) -> str:
    return f"""[Unit]
Description=Optional Duskie iPhone clipboard sharing
PartOf=graphical-session.target
After=graphical-session.target
ConditionEnvironment=WAYLAND_DISPLAY

[Service]
Type=exec
ExecStart={quoted(sys.executable)} {quoted(ROOT / 'phone_sync.py')} --config {quoted(config)}
Restart=on-failure
RestartSec=3
TimeoutStopSec=5
KillMode=control-group
UMask=0077
Slice=app.slice

[Install]
WantedBy=graphical-session.target
"""


def port(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError("Port must be 1–65535")
    return number


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=("install", "uninstall", "status"), default="install")
    parser.add_argument("--host", help="iPhone Wi-Fi address, or 127.0.0.1 for a USB tunnel")
    parser.add_argument("--port", type=port, default=22)
    parser.add_argument("--user", default="mobile")
    parser.add_argument("--identity", type=Path, default=Path.home() / ".ssh/id_ed25519")
    parser.add_argument("--usb-port", type=port, help="Optional fallback to an existing local iproxy SSH tunnel")
    parser.add_argument("--pair", action="store_true", help="Install the SSH public key using ssh-copy-id; asks for the phone password")
    args = parser.parse_args()
    if args.action == "status":
        subprocess.run(["systemctl", "--user", "status", "--no-pager", UNIT], check=False)
        return
    if args.action == "uninstall":
        subprocess.run(["systemctl", "--user", "disable", "--now", UNIT], check=False)
        if CONFIG.exists():
            config = json.loads(CONFIG.read_text(encoding="utf-8"))
            try:
                result = subprocess.run(ssh(config, "--disable"), check=False, timeout=10)
                offline = bool(result.returncode)
            except subprocess.TimeoutExpired:
                offline = True
            if offline:
                print("Phone offline: turn Phone clipboard sync Off in Duskie settings when available.")
        SERVICE.unlink(missing_ok=True)
        CONFIG.unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        print("Phone sync removed. Existing clipboard services and history retained.")
        return
    if not args.host or args.host.startswith("-") or any(c.isspace() or c in "@/" for c in args.host):
        parser.error("Provide --host with the phone address")
    if args.user != "mobile":
        parser.error("Use the iPhone mobile account, which owns the clipboard socket")
    identity = args.identity.expanduser().resolve()
    if not identity.is_file():
        parser.error(f"SSH identity missing: {identity}; create an SSH key first")
    for command in ("ssh", "systemctl", "wl-paste", "wl-copy"):
        if not shutil.which(command):
            parser.error(f"Install the missing command: {command}")
    config = {"host": args.host, "port": args.port, "user": args.user,
              "identity": str(identity), "usb_port": args.usb_port}
    if args.pair:
        subprocess.run(["ssh-copy-id", "-i", str(identity) + ".pub", "-p", str(args.port),
                        f"{args.user}@{args.host}"], check=True)
    # Check the installed helper before creating or enabling any Linux unit.
    subprocess.run(ssh(config, "--enable"), check=True, timeout=10)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    CONFIG.chmod(0o600)
    SERVICE.parent.mkdir(parents=True, exist_ok=True)
    SERVICE.write_text(unit(CONFIG), encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", UNIT], check=True)
    subprocess.run(["systemctl", "--user", "restart", UNIT], check=True)
    print("Phone clipboard sync enabled. Only new text copies are shared.")
    print(f"Status: systemctl --user status {UNIT}")


if __name__ == "__main__":
    main()
