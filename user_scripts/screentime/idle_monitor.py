"""Small ext-idle-notify-v1 client; no input devices, subprocesses or threads.

Only registry, seat and idle objects are bound. These interfaces transfer no
file descriptors. Messages use the documented native-endian Wayland wire format.
Each new connection waits for fresh input before reporting an active session.
"""

import os
import select
import socket
import struct
import time
from pathlib import Path


class IdleMonitor:
    def __init__(self, timeout_seconds: float) -> None:
        self.idle = False
        self._activity_seen = False
        self._buffer = bytearray()
        self._globals: dict[str, tuple[int, int]] = {}
        self._notifications: set[int] = set()
        self._sync_done = False
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            display = os.environ.get("WAYLAND_DISPLAY", "wayland-0")
            path = Path(display)
            if not path.is_absolute():
                path = Path(os.environ["XDG_RUNTIME_DIR"]) / path
            self._socket.settimeout(1.0)
            self._socket.connect(str(path))
            self._send(1, 1, struct.pack("=I", 2))  # wl_display.get_registry
            self._send(1, 0, struct.pack("=I", 3))  # wl_display.sync
            deadline = time.monotonic() + 1.0
            while not self._sync_done:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Wayland registry roundtrip timed out")
                self._socket.settimeout(remaining)
                self._receive()
            if "wl_seat" not in self._globals or "ext_idle_notifier_v1" not in self._globals:
                raise RuntimeError("compositor does not advertise seat and idle notifier")
            if self._globals["ext_idle_notifier_v1"][1] < 2:
                raise RuntimeError("input idle notifications require ext-idle-notify-v1 version 2")
            self._bind("wl_seat", 4, 1)
            self._bind("ext_idle_notifier_v1", 5, 2)
            milliseconds = max(1, int(timeout_seconds * 1000))
            # Input-only notifications ignore application idle inhibitors.
            self._send(5, 2, struct.pack("=III", 6, milliseconds, 4))
            self._notifications.add(6)
            # A new notification starts its timer now, regardless of prior idle
            # time. Wait for fresh input before trusting that initial state.
            # Hyprland's zero-timeout notifications never emit resumed.
            self._send(5, 2, struct.pack("=III", 7, 1, 4))
            self._socket.setblocking(False)
        except BaseException:
            self.close()
            raise

    def _send(self, object_id: int, opcode: int, payload: bytes) -> None:
        self._socket.sendall(struct.pack("=II", object_id, ((len(payload) + 8) << 16) | opcode) + payload)

    def _bind(self, interface: str, object_id: int, version: int) -> None:
        name = interface.encode() + b"\0"
        padded = name + b"\0" * (-len(name) % 4)
        global_id = self._globals[interface][0]
        self._send(2, 0, struct.pack("=II", global_id, len(name)) + padded + struct.pack("=II", version, object_id))

    def _receive(self) -> None:
        chunk = self._socket.recv(65536)
        if not chunk:
            raise ConnectionError("Wayland connection closed")
        self._buffer.extend(chunk)
        while len(self._buffer) >= 8:
            object_id, header = struct.unpack_from("=II", self._buffer)
            size, opcode = header >> 16, header & 0xFFFF
            if size < 8 or size % 4:
                raise RuntimeError("invalid Wayland message")
            if len(self._buffer) < size:
                break
            payload = self._buffer.take_bytes(size)[8:]
            if object_id == 1 and opcode == 0:
                raise RuntimeError(f"Wayland protocol error: {payload!r}")
            if object_id == 2 and opcode == 0:
                global_id, length = struct.unpack_from("=II", payload)
                interface = payload[8:8 + length - 1].decode()
                version = struct.unpack_from("=I", payload, 8 + ((length + 3) & ~3))[0]
                self._globals.setdefault(interface, (global_id, version))
            elif object_id == 2 and opcode == 1:
                removed = struct.unpack_from("=I", payload)[0]
                if any(self._globals[name][0] == removed for name in ("wl_seat", "ext_idle_notifier_v1") if name in self._globals):
                    raise ConnectionError("Wayland idle global removed")
            elif object_id == 3 and opcode == 0:
                self._sync_done = True
            elif object_id == 7 and opcode == 1 and not self._activity_seen:
                self._activity_seen = True
                self._send(7, 0, b"")  # destroy the startup-only notification
            elif object_id in self._notifications:
                if opcode == 0:
                    self.idle = True
                elif opcode == 1:
                    self.idle = False

    def poll(self) -> bool:
        while select.select([self._socket], [], [], 0)[0]:
            self._receive()
        return self.idle or not self._activity_seen

    def close(self) -> None:
        self._socket.close()
