#!/usr/bin/env python3
import os
import sys
import json
import socket
import signal
import asyncio
import compileall
import importlib
import ctypes
import gc
from pathlib import Path
from typing import Any, Final
from multiprocessing.shared_memory import SharedMemory

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# PEP 695 Type Aliases
type JSONDict = dict[str, Any]

# Architectural Constants
MAX_PAYLOAD_SIZE: Final[int] = 1024 * 1024  # 1MB security limit against OOM payload bombs

class DuskyDaemon:
    __slots__ = (
        "uid", "sock_path", "server", "shutdown_event", 
        "cache", "watch_paths", "is_warmed", "bg_watch", "bg_compile", "_idle_timer", "_shm_blocks"
    )

    def __init__(self) -> None:
        self.uid: int = os.getuid()
        self.sock_path: str = f"/run/user/{self.uid}/dusky.sock"
        if not os.path.exists(f"/run/user/{self.uid}"):
            self.sock_path = f"/tmp/dusky_{self.uid}.sock"
        
        self.server: asyncio.Server | None = None
        self.shutdown_event: asyncio.Event = asyncio.Event()
        self.cache: JSONDict = {}
        self._shm_blocks: dict[str, SharedMemory] = {}
        self.is_warmed: bool = False
        self._idle_timer: asyncio.TimerHandle | None = None
        self.bg_watch: asyncio.Task | None = None
        self.bg_compile: asyncio.Task | None = None
        self.watch_paths: list[Path] = [
            Path("~/user_scripts").expanduser().resolve(),
            Path("~/.config/dusky_schema").expanduser().resolve(),
            Path("~/Documents/schemas").expanduser().resolve(),
        ]

    def _verify_socket_lock(self) -> None:
        if os.path.exists(self.sock_path):
            try:
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                probe.connect(self.sock_path)
                probe.close()
                sys.stdout.write("Daemon is already running. Exiting.\n")
                sys.exit(0)
            except (socket.error, ConnectionRefusedError):
                try:
                    os.unlink(self.sock_path)
                except OSError:
                    pass

    def _reset_idle_timer(self) -> None:
        if self._idle_timer:
            self._idle_timer.cancel()
        try:
            loop = asyncio.get_running_loop()
            # 15 minutes of zero activity triggers memory decay
            self._idle_timer = loop.call_later(900.0, self._trigger_decay)
        except RuntimeError:
            pass

    def _trigger_decay(self) -> None:
        asyncio.create_task(self._async_decay_memory())

    async def _async_decay_memory(self) -> None:
        if not self.is_warmed:
            return
        
        sys._clear_type_cache()
        gc.collect(2)
        
        try:
            await asyncio.to_thread(ctypes.CDLL("libc.so.6").malloc_trim, 0)
        except (OSError, AttributeError):
            pass
            
        self.is_warmed = False

    def _trigger_on_demand_warmup(self) -> None:
        self._reset_idle_timer()
        if self.is_warmed:
            return
        self.is_warmed = True
        
        targets = [
            "textual", "rich", "asyncio", "json", "argparse",
            "python.frontend.core_types", "python.frontend.ui"
        ]
        for mod in targets:
            try:
                importlib.import_module(mod)
            except (ImportError, Exception):
                pass

        self.bg_compile = asyncio.create_task(self._compile_bytecode())
        self.bg_watch = asyncio.create_task(self._watch_files())

    def _scan_filesystem(self, last_mtimes: dict[str, float]) -> None:
        """Blocking I/O operation designed to be run in a separate thread."""
        for path in self.watch_paths:
            if not path.exists():
                continue
            try:
                with os.scandir(str(path)) as it:
                    for entry in it:
                        try:
                            if entry.name.endswith(".py") or entry.name.endswith(".conf"):
                                mtime = entry.stat().st_mtime
                                file_str = entry.path
                                if last_mtimes.get(file_str) != mtime:
                                    last_mtimes[file_str] = mtime
                                    self.cache[file_str] = {"status": "hot", "timestamp": mtime}
                        except OSError:
                            pass
            except OSError:
                pass

    async def _watch_files(self) -> None:
        last_mtimes: dict[str, float] = {}
        while not self.shutdown_event.is_set():
            await asyncio.to_thread(self._scan_filesystem, last_mtimes)
            try:
                await asyncio.wait_for(self.shutdown_event.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                continue

    async def _compile_bytecode(self) -> None:
        for path in self.watch_paths:
            if path.exists():
                try:
                    await asyncio.to_thread(compileall.compile_dir, str(path), 10, None, True, 1)
                except Exception:
                    pass

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            self._trigger_on_demand_warmup()

            raw_len = await reader.readexactly(4)
            msg_len = int.from_bytes(raw_len, "big")
            
            # Security guard against OOM payload bombs
            if msg_len > MAX_PAYLOAD_SIZE:
                return

            payload = await reader.readexactly(msg_len)
            request: JSONDict = json.loads(payload.decode("utf-8"))

            response: JSONDict = {}
            match request.get("command"):
                case "status":
                    response = {
                        "status": "active",
                        "warmed": self.is_warmed,
                        "cached_files": len(self.cache)
                    }
                case "get_schema":
                    target = request.get("target", "")
                    response = {"data": self.cache.get(target, {})}
                case "stop":
                    response = {"status": "stopping"}
                    self.shutdown_event.set()
                case _:
                    response = {"error": "unknown_command"}

            resp_bytes = json.dumps(response).encode("utf-8")
            writer.write(len(resp_bytes).to_bytes(4, "big") + resp_bytes)
            await writer.drain()

        except (asyncio.IncompleteReadError, json.JSONDecodeError, OSError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def start(self) -> None:
        listen_fds = int(os.environ.get("LISTEN_FDS", 0))
        if listen_fds > 0:
            sock = socket.fromfd(3, socket.AF_UNIX, socket.SOCK_STREAM)
            os.environ.pop("LISTEN_FDS", None)
            self.server = await asyncio.start_unix_server(self._handle_client, sock=sock)
        else:
            self._verify_socket_lock()
            self.server = await asyncio.start_unix_server(self._handle_client, path=self.sock_path)
        
        self._reset_idle_timer()
        await self.shutdown_event.wait()
        
        if self._idle_timer:
            self._idle_timer.cancel()
        if self.bg_compile:
            self.bg_compile.cancel()
        if self.bg_watch:
            self.bg_watch.cancel()

        if self.server:
            self.server.close()
            await self.server.wait_closed()

        for shm in self._shm_blocks.values():
            try:
                shm.close()
                shm.unlink()
            except Exception:
                pass
        self._shm_blocks.clear()
        
        if not int(os.environ.get("LISTEN_FDS", 0)) and os.path.exists(self.sock_path):
            try:
                os.unlink(self.sock_path)
            except OSError:
                pass

def main() -> None:
    daemon = DuskyDaemon()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, daemon.shutdown_event.set)
        except NotImplementedError:
            pass
        
    try:
        loop.run_until_complete(daemon.start())
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        loop.close()

if __name__ == "__main__":
    main()
