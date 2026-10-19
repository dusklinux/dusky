#!/usr/bin/env python3
"""Optional, event-driven Wayland ↔ Duskie text clipboard service."""
import json
import os
import socket
import sys
lazy import argparse
lazy import asyncio
lazy import logging
lazy import signal
lazy import uuid
lazy from pathlib import Path

MAX_TEXT = 16384
MAX_FRAME = 131072


def encode(message: dict) -> bytes:
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if not 0 < len(body) <= MAX_FRAME:
        raise ValueError("Invalid frame size")
    return len(body).to_bytes(4, "big") + body


def valid_text(text: object) -> bool:
    if not isinstance(text, str) or not text:
        return False
    try:
        return len(text.encode("utf-8")) <= MAX_TEXT
    except UnicodeError:
        return False


async def read_frame(reader: asyncio.StreamReader) -> dict:
    size = int.from_bytes(await reader.readexactly(4), "big")
    if not 0 < size <= MAX_FRAME:
        raise ValueError("Invalid frame size")
    message = json.loads(await reader.readexactly(size))
    if not isinstance(message, dict):
        raise ValueError("Expected a message object")
    return message


def capture(path: str) -> None:
    # wl-paste invokes this only on changes. Preserve all bytes, including final
    # newlines; no command substitution, temporary file or clipboard polling.
    state = os.environ.get("CLIPBOARD_STATE", "nil")
    text = ""
    if state in ("data", "sensitive"):
        raw = sys.stdin.buffer.read(MAX_TEXT + 1)
        try:
            text = raw.decode("utf-8") if len(raw) <= MAX_TEXT else ""
        except UnicodeError:
            pass
    message = {"type": "local", "text": text}
    try:
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(2)
            connection.connect(path)
            connection.sendall(encode(message))
    except OSError:
        pass  # Service stopping; the existing clipboard owner is unaffected.


def ssh_command(config: dict, host: str, port: int, action: str) -> list[str]:
    return ["ssh", "-T", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=2", "-o", "LogLevel=ERROR",
            "-i", config["identity"], "-p", str(port),
            f'{config["user"]}@{host}', "/var/jb/usr/libexec/duskie-clipboard", action]


async def stop_process(process: asyncio.subprocess.Process | None) -> None:
    if process is None:
        return

    async def discard(stream: asyncio.StreamReader) -> None:
        while await stream.read(65536):
            pass

    # A full, paused stdout pipe can outlive wait(), or prevent it finishing.
    # Drain discarded output concurrently without accumulating it in memory.
    async with asyncio.TaskGroup() as group:
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                group.create_task(discard(stream))
        if process.returncode is None:
            try:
                process.terminate()
                async with asyncio.timeout(2):
                    await process.wait()
            except TimeoutError:
                process.kill()
                await process.wait()
            except ProcessLookupError:
                await process.wait()


async def read_clipboard() -> str | None:
    # Establish startup state even when the text watcher has no initial offer
    # (for example, an image). Read once, with bounded bytes and waiting time.
    process = await asyncio.create_subprocess_exec(
        "wl-paste", "--no-newline", "--type", "text",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        limit=MAX_TEXT + 1)
    try:
        async with asyncio.timeout(3):
            try:
                raw = await process.stdout.readexactly(MAX_TEXT + 1)
            except asyncio.IncompleteReadError as error:
                raw = error.partial
            if len(raw) > MAX_TEXT or await process.wait():
                return None
            try:
                text = raw.decode("utf-8")
            except UnicodeError:
                return None
            return text if text else None
    finally:
        await stop_process(process)


class Sync:
    def __init__(self, config: dict):
        self.config = config
        self.writer: asyncio.StreamWriter | None = None
        self.last_text: str | None = None
        self.retry_delay = 1
        self.pending: dict[str, asyncio.Future] = {}
        self.copies: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        self.socket_path = Path(os.environ["XDG_RUNTIME_DIR"]) / "dusky-phone-sync.sock"

    async def local(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            async with asyncio.timeout(3):
                message = await read_frame(reader)
            text = message.get("text")
            if message.get("type") != "local" or not isinstance(text, str):
                return
            if not valid_text(text):
                self.last_text = None
                return
            if text == self.last_text:
                return
            self.last_text = text
            if self.writer is not None:
                if self.copies.full():
                    self.copies.get_nowait()
                self.copies.put_nowait(text)
        except (OSError, ValueError, asyncio.IncompleteReadError, TimeoutError):
            logging.warning("Ignored an incomplete local clipboard event")
        finally:
            writer.close()
            await writer.wait_closed()

    async def send_copies(self) -> None:
        while True:
            text = await self.copies.get()
            identifier = str(uuid.uuid4())
            acknowledged = asyncio.get_running_loop().create_future()
            self.pending[identifier] = acknowledged
            try:
                self.writer.write(encode({"type": "copy", "id": identifier, "text": text}))
                await self.writer.drain()
                async with asyncio.timeout(15):
                    await acknowledged
                logging.info("Linux → iPhone copy acknowledged (%d bytes)", len(text.encode("utf-8")))
            finally:
                self.pending.pop(identifier, None)

    async def receive_copies(self, reader: asyncio.StreamReader) -> None:
        while True:
            message = await read_frame(reader)
            identifier = message.get("id")
            if not isinstance(identifier, str):
                raise ValueError("Missing message identifier")
            uuid.UUID(identifier)
            if message.get("type") == "ping":
                self.writer.write(encode({"type": "pong", "id": identifier}))
                await self.writer.drain()
                continue
            if message.get("type") == "ack":
                future = self.pending.get(identifier)
                if future is not None and not future.done():
                    future.set_result(None)
                continue
            text = message.get("text")
            if message.get("type") != "copy" or not valid_text(text):
                raise ValueError("Invalid remote clipboard event")
            if text != self.last_text:
                previous = self.last_text
                self.last_text = text  # Arm echo suppression before wl-copy.
                process = await asyncio.create_subprocess_exec(
                    "wl-copy", "--type", "text/plain;charset=utf-8",
                    stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                try:
                    async with asyncio.timeout(5):
                        await process.communicate(text.encode("utf-8"))
                    if process.returncode:
                        raise OSError("wl-copy failed")
                except BaseException:
                    self.last_text = previous
                    await stop_process(process)
                    raise
                logging.info("iPhone → Linux copy applied (%d bytes)", len(text.encode("utf-8")))
            self.writer.write(encode({"type": "ack", "id": identifier}))
            await self.writer.drain()

    async def connect(self, host: str, port: int) -> None:
        process = await asyncio.create_subprocess_exec(
            *ssh_command(self.config, host, port, "--stream"),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=None)
        try:
            async with asyncio.timeout(8):
                hello = await read_frame(process.stdout)
            if hello != {"type": "hello", "version": 1, "maxBytes": MAX_TEXT}:
                raise ValueError("Unsupported phone protocol")
            self.retry_delay = 1
            self.writer = process.stdin
            logging.info("Connected to iPhone via %s:%d", host, port)
            async with asyncio.TaskGroup() as group:
                group.create_task(self.send_copies())
                group.create_task(self.receive_copies(process.stdout))
        finally:
            self.writer = None
            while not self.copies.empty():
                self.copies.get_nowait()
            self.pending.clear()
            await stop_process(process)

    async def reconnect(self) -> None:
        endpoints = [(self.config["host"], self.config["port"])]
        if self.config.get("usb_port"):
            endpoints.append(("127.0.0.1", self.config["usb_port"]))
        while True:
            for host, port in endpoints:
                try:
                    await self.connect(host, port)
                except (ExceptionGroup, OSError, ValueError, TimeoutError, asyncio.IncompleteReadError) as error:
                    logging.warning("Phone connection unavailable via %s:%d (%s); retrying",
                                    host, port, error)
            await asyncio.sleep(self.retry_delay)
            self.retry_delay = min(self.retry_delay * 2, 30)

    async def run(self) -> None:
        self.last_text = await read_clipboard()
        self.socket_path.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(self.local, path=self.socket_path, limit=MAX_FRAME + 4)
        self.socket_path.chmod(0o600)
        watcher = None
        try:
            watcher = await asyncio.create_subprocess_exec(
                "wl-paste", "--no-newline", "--type", "text", "--watch",
                sys.executable, str(Path(__file__).resolve()), "--capture", str(self.socket_path),
                stderr=asyncio.subprocess.DEVNULL)
            async with server, asyncio.TaskGroup() as group:
                group.create_task(self.reconnect())
                await watcher.wait()
                raise OSError("Clipboard watcher exited; restarting service")
        finally:
            server.close()
            await server.wait_closed()
            await stop_process(watcher)
            self.socket_path.unlink(missing_ok=True)


async def run(config: dict) -> None:
    task = asyncio.create_task(Sync(config).run())
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, task.cancel)
    try:
        await task
    except asyncio.CancelledError:
        pass


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--capture":
        capture(sys.argv[2])
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
