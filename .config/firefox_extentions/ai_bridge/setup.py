#!/usr/bin/env python3
"""Local AI Web Bridge — installer and environment manager (Python 3.15+, Linux only).

Registers and enables localai_bridge.service as a systemd user daemon,
validates the WebSocket wire handshake, and checks active Firefox status.

Usage:
    python3 setup.py            # Install / repair & start systemd daemon
    python3 setup.py --check    # Inspect daemon, systemd, and extension status
    python3 setup.py --build    # Build zip package for about:debugging
    python3 setup.py --remove   # Uninstall & stop systemd service
"""

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Final
import zipfile

HERE: Final = Path(__file__).resolve().parent
BRIDGE_PY: Final = HERE / "bridge.py"
SERVICE_NAME: Final = "localai_bridge.service"
SERVICE_SRC: Final = HERE / SERVICE_NAME
SYSTEMD_USER_DIR: Final = Path.home() / ".config" / "systemd" / "user"
SYSTEMD_TARGET: Final = SYSTEMD_USER_DIR / SERVICE_NAME
HOST: Final = "127.0.0.1"
PORT: Final = 8765


def probe_socket(timeout: float = 0.5) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect((HOST, PORT))
            return True
    except (ConnectionRefusedError, OSError):
        return False


def probe_handshake(timeout: float = 2.0) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect((HOST, PORT))
            s.sendall(
                b"GET / HTTP/1.1\r\n"
                b"Host: 127.0.0.1:8765\r\n"
                b"Upgrade: websocket\r\n"
                b"Connection: Upgrade\r\n"
                b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                b"Sec-WebSocket-Version: 13\r\n\r\n"
            )
            resp = s.recv(1024)
            return b"101" in resp.split(b"\r\n", 1)[0]
    except Exception:
        return False


def build_package() -> Path:
    dist_dir = HERE / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    out_zip = dist_dir / "ai_bridge.zip"
    files = ["manifest.json", "background.js", "content_isolated.js", "content_main.js"]
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for fname in files:
            p = HERE / fname
            if p.is_file():
                z.write(p, arcname=fname)
    print(f"   built package → {out_zip}")
    return out_zip


def check_status() -> int:
    print("== Local AI Web Bridge Diagnostic ==")
    print(f"Python: {sys.version.split()[0]} ({sys.executable})")

    # Service status
    is_installed = SYSTEMD_TARGET.is_file() or SYSTEMD_TARGET.is_symlink()
    print(f"Systemd unit installed: {'YES' if is_installed else 'NO'} ({SYSTEMD_TARGET})")

    active = False
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", SERVICE_NAME], capture_output=True, text=True, timeout=2)
        active = r.stdout.strip() == "active"
    except Exception:
        pass
    print(f"Systemd service active: {'YES (running)' if active else 'NO'}")

    # Port probe
    port_open = probe_socket()
    print(f"WebSocket port ({HOST}:{PORT}): {'OPEN' if port_open else 'CLOSED'}")

    if port_open:
        handshake_ok = probe_handshake()
        print(f"WebSocket handshake: {'OK (101 Switching Protocols)' if handshake_ok else 'FAILED'}")

    # Firefox status
    ff_running = False
    try:
        r = subprocess.run(["pgrep", "-x", "firefox"], capture_output=True, timeout=2)
        ff_running = r.returncode == 0
    except Exception:
        pass
    print(f"Firefox process: {'RUNNING' if ff_running else 'NOT RUNNING'}")

    return 0 if (is_installed and active and port_open) else 1


def install() -> int:
    print("== Installing Local AI Web Bridge ==")
    if not BRIDGE_PY.is_file():
        print(f"!! Missing {BRIDGE_PY}", file=sys.stderr)
        return 1
    BRIDGE_PY.chmod(0o755)

    # 1. Install systemd user service
    SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
    if not SERVICE_SRC.is_file():
        print(f"!! Missing {SERVICE_SRC}", file=sys.stderr)
        return 1

    content = SERVICE_SRC.read_text(encoding="utf-8")
    SYSTEMD_TARGET.write_text(content, encoding="utf-8")
    SYSTEMD_TARGET.chmod(0o644)
    print(f"   installed unit → {SYSTEMD_TARGET}")

    # 2. Reload daemon
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    print("   systemctl --user daemon-reload OK")

    # 3. Enable and restart service
    subprocess.run(["systemctl", "--user", "enable", "--now", SERVICE_NAME], check=True)
    print(f"   enabled and started {SERVICE_NAME}")

    # 4. Wait for port to open
    print("   waiting for daemon port...", end="", flush=True)
    for _ in range(30):
        if probe_socket(0.2):
            print(" ready!")
            break
        time.sleep(0.1)
    else:
        print(" TIMEOUT!")
        return 1

    # 5. Handshake probe
    if probe_handshake():
        print(f"   probe WebSocket handshake OK on {HOST}:{PORT}")
    else:
        print(f"!! probe WebSocket handshake failed", file=sys.stderr)
        return 1

    build_package()
    print("\nLocal AI Web Bridge is installed, enabled, and running!")
    print("To test queries: ask \"hi\" or python3 bridge.py \"hi\"")
    return 0


def remove() -> int:
    print("== Removing Local AI Web Bridge service ==")
    try:
        subprocess.run(["systemctl", "--user", "stop", SERVICE_NAME], check=False)
        subprocess.run(["systemctl", "--user", "disable", SERVICE_NAME], check=False)
    except Exception:
        pass

    if SYSTEMD_TARGET.is_file() or SYSTEMD_TARGET.is_symlink():
        SYSTEMD_TARGET.unlink(missing_ok=True)
        print(f"   removed {SYSTEMD_TARGET}")

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    print("   disabled and removed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Local AI Web Bridge Installer")
    parser.add_argument("--check", action="store_true", help="Check status only")
    parser.add_argument("--build", action="store_true", help="Build zip package")
    parser.add_argument("--remove", action="store_true", help="Remove systemd service")
    args = parser.parse_args()

    if args.check:
        return check_status()
    if args.build:
        build_package()
        return 0
    if args.remove:
        return remove()

    return install()


if __name__ == "__main__":
    sys.exit(main())
