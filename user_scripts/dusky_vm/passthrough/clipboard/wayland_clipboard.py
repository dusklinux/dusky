#!/usr/bin/env python3
"""Event-driven, bidirectional Wayland text clipboard over a persistent SSH pipe.

Host: wayland_clipboard.py -- ssh [options] user@guest python3 PATH --remote
Guest endpoint uses the same file. Requires wl-clipboard on both ends and a
compositor supporting data-control (Hyprland). No X11 or clipboard polling.
The endpoint intentionally uses syntax also accepted by the supplied live ISO's
Python 3.14; the host deployment code targets Python 3.15.
"""

import argparse
import base64
import fcntl
import json
import os
import pwd
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

MAX_TEXT = 8 << 20


def notify(message: str) -> None:
    print(message, file=sys.stderr, flush=True)
    address = os.environ.get("NOTIFY_SOCKET")
    if address:
        address = "\0" + address[1:] if address.startswith("@") else address
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
                sock.sendto(f"STATUS={message}".encode(), address)
        except OSError:
            pass


def current_text(env: dict[str, str] | None = None) -> bytes | None:
    current = subprocess.run(["wl-paste", "--no-newline", "--type", "text"],
                             capture_output=True, timeout=5, check=False, env=env)
    data = current.stdout
    return data if current.returncode == 0 and len(data) <= MAX_TEXT else None


def emit() -> None:
    if os.environ.get("CLIPBOARD_STATE") in {"nil", "clear", "sensitive"}:
        return
    # Send only a notification. Sampling belongs in the bridge when it consumes
    # this event: a large payload can otherwise become stale while queued in a
    # pipe. Drain incrementally without retaining an extra clipboard-sized copy.
    while sys.stdin.buffer.read(65536):
        pass
    print('{"changed":true}', flush=True)


def session_environment(remote: bool) -> dict[str, str]:
    env = os.environ.copy()
    runtime = Path(env.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    display = env.get("WAYLAND_DISPLAY")
    if display and (runtime / display).exists() and all(shutil.which(name) for name in ("wl-copy", "wl-paste")):
        return env
    # A live guest can have an SSH root account and a regular desktop user.
    # Select the unique active Wayland session and run clipboard tools as its UID.
    while True:
        roots = Path("/run/user").glob("[0-9]*") if remote and os.geteuid() == 0 else [runtime]
        sockets = [p for root in roots for p in root.glob("wayland-*")
                   if (p.name.removeprefix("wayland-").isdecimal()
                       or p.with_name(p.name + ".lock").is_file())
                   and stat.S_ISSOCK(p.stat().st_mode)]
        if len(sockets) == 1 and all(shutil.which(name) for name in ("wl-copy", "wl-paste")):
            path = sockets[0]
            user = pwd.getpwuid(path.stat().st_uid)
            if os.geteuid() == 0 and user.pw_uid != 0:
                os.initgroups(user.pw_name, user.pw_gid)
                os.setgid(user.pw_gid)
                os.setuid(user.pw_uid)
            env.update(XDG_RUNTIME_DIR=str(path.parent), WAYLAND_DISPLAY=path.name,
                       HOME=user.pw_dir, USER=user.pw_name)
            return env
        time.sleep(2)


def bridge(command: list[str], remote: bool) -> int:
    if remote:
        print(json.dumps({"status": "Waiting for guest Wayland session and wl-clipboard"}), flush=True)
    else:
        notify("Waiting for host Wayland session and wl-clipboard")
    env = session_environment(remote)
    # Ignore the guest's pre-existing clipboard on connect. The host's current
    # selection wins; a reconnect must not replace it with stale guest text.
    initial = current_text(env) if remote else None
    watcher = subprocess.Popen(
        ["wl-paste", "--type", "text", "--watch", sys.executable, str(Path(__file__).resolve()), "--emit"],
        stdout=subprocess.PIPE, env=env, start_new_session=True,
    )
    peer = None
    try:
        if remote:
            incoming, outgoing = sys.stdin.buffer, sys.stdout.buffer
            print(json.dumps({"status": "Clipboard ready: host ↔ guest (text)"}), flush=True)
        else:
            notify("Connecting to guest clipboard endpoint")
            peer = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            incoming, outgoing = peer.stdout, peer.stdin
        buffers: dict[str, bytearray] = {"local": bytearray(), "peer": bytearray()}
        pending = bytearray()
        os.set_blocking(outgoing.fileno(), False)
        last = initial
        revision = 0
        with selectors.DefaultSelector() as selector:
            selector.register(watcher.stdout, selectors.EVENT_READ, "local")
            selector.register(incoming, selectors.EVENT_READ, "peer")
            def enqueue(data: bytes) -> None:
                if not pending:
                    selector.register(outgoing, selectors.EVENT_WRITE, "send")
                pending.extend(json.dumps({"text": base64.b64encode(data).decode("ascii"),
                                           "revision": revision}).encode() + b"\n")
                if len(pending) > MAX_TEXT * 4:
                    raise RuntimeError("Clipboard peer is not consuming updates.")

            while True:
                for key, _mask in selector.select():
                    if key.data == "send":
                        try:
                            sent = os.write(key.fd, pending[:65536])
                        except BlockingIOError:
                            continue
                        del pending[:sent]
                        if not pending:
                            selector.unregister(outgoing)
                        continue
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        return 1 if key.data == "local" else 0
                    buffer = buffers[key.data]
                    buffer.extend(chunk)
                    if len(buffer) > MAX_TEXT * 2:
                        raise RuntimeError("Clipboard frame exceeds the text limit.")
                    while (end := buffer.find(b"\n")) >= 0:
                        frame = bytes(buffer[:end])
                        del buffer[:end + 1]
                        packet = json.loads(frame)
                        if "status" in packet:
                            if not remote:
                                notify(packet["status"])
                            continue
                        if key.data == "local":
                            data = current_text(env)
                            if data is None:
                                continue
                        else:
                            data = base64.b64decode(packet["text"], validate=True)
                            if remote:
                                revision = packet["revision"]
                            else:
                                if packet["revision"] != revision:
                                    continue
                                # Another VM or app may have updated the host
                                # before its watcher notification was consumed.
                                # Publish that copy first and reject the old echo.
                                current = current_text(env)
                                if current is not None and current != last:
                                    last = current
                                    revision += 1
                                    enqueue(current)
                                    continue
                        if len(data) > MAX_TEXT:
                            raise RuntimeError("Clipboard text exceeds 8 MiB.")
                        if data == last:
                            continue
                        last = data
                        if key.data == "local":
                            if not remote:
                                revision += 1
                            enqueue(data)
                        else:
                            # wl-copy's parent returns once selection ownership
                            # is established. Wait for that handshake so a later
                            # update cannot race a still-starting clipboard owner.
                            subprocess.run(["wl-copy", "--type", "text/plain;charset=utf-8"],
                                           input=data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           env=env, timeout=5, check=True)
    finally:
        if watcher.poll() is None:
            os.killpg(watcher.pid, signal.SIGTERM)
        watcher.wait(timeout=5)
        watcher.stdout.close()
        if peer is not None:
            peer.terminate()
            peer.wait(timeout=5)
            peer.stdin.close()
            peer.stdout.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emit", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--remote", action="store_true")
    parser.add_argument("--lock", type=Path, help="prevent duplicate host bridges for a VM")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    def stop(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGHUP, stop)
    try:
        if args.emit:
            emit()
            return 0
        lock = None
        if args.lock:
            lock = args.lock.open("a")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("Clipboard sharing is already running for this VM.", file=sys.stderr)
                return 0
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not args.remote and not command:
            parser.error("specify an SSH command after --")
        return bridge(command, args.remote)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Wayland clipboard: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
