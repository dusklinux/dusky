#!/usr/bin/env python3
"""Discover and control MPV's documented JSON IPC, including embedded players.

No application names, socket filename patterns or user paths are assumed.
The public identity includes Linux process start time to reject reused PIDs.
"""

import json
import os
from pathlib import Path
import socket
import sys
import time


class CommandError(ValueError):
    """MPV rejected a command or could not currently provide a property."""


def process(pid: int) -> tuple[int, str, Path | None]:
    directory = Path("/proc") / str(pid)
    fields = (directory / "stat").read_text().rsplit(") ", 1)[1].split()
    arguments = (directory / "cmdline").read_bytes().split(b"\0")
    path = None
    for index, raw in enumerate(arguments):
        argument = os.fsdecode(raw)
        if argument == "--":
            break
        if argument.startswith("--input-ipc-server="):
            path = Path(argument.partition("=")[2])
        elif argument == "--input-ipc-server" and index + 1 < len(arguments):
            path = Path(os.fsdecode(arguments[index + 1]))
    if path is not None and not path.is_absolute():
        path = directory / "cwd" / path
    return int(fields[1]), fields[19], path


def discover() -> list[dict]:
    players = []
    for directory in Path("/proc").glob("[0-9]*"):
        try:
            if directory.stat().st_uid != os.getuid() or (directory / "comm").read_text().strip() != "mpv":
                continue
            pid = int(directory.name)
            parent, started, path = process(pid)
            if path is None or not path.is_socket():
                continue
            ancestors = []
            while parent > 1 and parent not in ancestors:
                ancestors.append(parent)
                try:
                    parent, _, _ = process(parent)
                except (OSError, ValueError, IndexError):
                    break
            players.append({"name": f"mpv.ipc-{pid}", "connection": f"mpv:{pid}:{started}",
                            "pid": pid, "ancestors": ancestors})
        except (OSError, ValueError, IndexError):
            continue  # Processes may exit while /proc is being enumerated.
    return players


class Client:
    def __init__(self, identity: str):
        backend, pid, started = identity.split(":")
        self.pid = int(pid)
        _, actual_started, path = process(self.pid)
        if backend != "mpv" or actual_started != started or path is None:
            raise ValueError("MPV instance is no longer available")
        self.connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.connection.settimeout(1)
        try:
            self.connection.connect(str(path))
            self.buffer = bytearray()
            self.sequence = 0
            if self.get("pid") != self.pid:
                raise ValueError("IPC socket belongs to another MPV instance")
        except BaseException:
            self.connection.close()
            raise

    def command(self, *arguments):
        self.sequence += 1
        request = {"command": arguments, "request_id": self.sequence}
        self.connection.sendall(json.dumps(request).encode() + b"\n")
        deadline = time.monotonic() + 1
        while True:
            while b"\n" in self.buffer:
                line, _, remainder = self.buffer.partition(b"\n")
                self.buffer = remainder
                reply = json.loads(line)
                if reply.get("request_id") != self.sequence:
                    continue  # Property/event messages can be interleaved.
                if reply.get("error") != "success":
                    raise CommandError(reply.get("error", "Invalid IPC reply"))
                return reply.get("data")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("MPV IPC reply timed out")
            self.connection.settimeout(remaining)
            chunk = self.connection.recv(65536)
            if not chunk:
                raise ConnectionError("MPV closed the IPC connection")
            self.buffer.extend(chunk)

    def get(self, name):
        return self.command("get_property", name)

    def snapshot(self):
        idle = self.get("idle-active")
        paused = self.get("pause")
        count = self.get("playlist-count")
        index = self.get("playlist-pos")
        title = ""
        if not idle:
            try:
                title = self.get("media-title")
            except CommandError:
                # Title metadata may be unavailable between playlist entries.
                # It must never turn an accepted playback command into failure.
                pass
        status = "Stopped" if idle else "Paused" if paused else "Playing"
        # Match busctl's typed GetAll envelope so the router has one parser.
        def value(kind, data):
            return {"type": kind, "data": data}
        return {"type": "a{sv}", "data": [{
            "PlaybackStatus": value("s", status),
            "CanControl": value("b", True), "CanPlay": value("b", count > 0),
            "CanPause": value("b", not idle),
            "CanGoNext": value("b", index >= 0 and index + 1 < count),
            "CanGoPrevious": value("b", index > 0),
            "Metadata": value("a{sv}", {} if idle and count == 0 else {
                "xesam:title": value("s", title),
                "mpris:trackid": value("o", f"/ipc/track/{max(index, 0)}"),
            }),
        }]}

    def control(self, action: str):
        if action == "Play":
            if self.get("idle-active"):
                if self.get("playlist-count") <= 0:
                    raise ValueError("No track to play")
                try:
                    stopped_id = self.get("user-data/osd-router-stop-id")
                except CommandError:
                    stopped_id = None
                # Entry IDs remain valid if the host app reorders the playlist;
                # a replacement playlist cannot inherit an old numeric index.
                playlist = self.get("playlist")
                index = next((i for i, entry in enumerate(playlist)
                              if entry.get("id") == stopped_id), max(self.get("playlist-pos"), 0))
                self.command("playlist-play-index", index)
            self.command("set_property", "pause", False)
        elif action == "Pause":
            self.command("set_property", "pause", True)
        elif action == "Next":
            self.command("playlist-next")
        elif action == "Previous":
            self.command("playlist-prev")
        elif action == "Stop":
            index = self.get("playlist-pos")
            if index >= 0:
                self.command("set_property", "user-data/osd-router-stop-id", self.get(f"playlist/{index}/id"))
            self.command("stop", "keep-playlist")
        else:
            raise ValueError(f"Unsupported action: {action}")


def main() -> int:
    try:
        if sys.argv[1:] == ["list"]:
            players = []
            for player in discover():
                try:
                    client = Client(player["connection"])
                    try:
                        player["properties"] = client.snapshot()
                    finally:
                        client.connection.close()
                    players.append(player)
                except (OSError, ValueError, IndexError):
                    continue
            print(json.dumps(players))
            return 0
        if len(sys.argv) not in (3, 4) or sys.argv[1] not in ("read", "control"):
            raise ValueError("Usage: media_ipc.py list | read ID | control ID ACTION")
        client = Client(sys.argv[2])
        try:
            if sys.argv[1] == "read":
                print(json.dumps(client.snapshot()))
            else:
                client.control(sys.argv[3])
                print(json.dumps(client.snapshot()))
        finally:
            client.connection.close()
        return 0
    except (OSError, ValueError, IndexError) as error:
        print(f"MPV IPC: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
