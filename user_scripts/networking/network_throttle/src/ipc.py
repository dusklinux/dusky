import asyncio
import json
import os
import grp
from typing import Dict, Any, Callable, Awaitable, Optional

from src.utils import logger

SOCKET_PATH = "/run/netctl.sock"

class IPCServer:
    """Asynchronous Unix domain socket server for receiving command RPCs from CLI/TUI."""
    
    def __init__(self, handler: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]):
        self.handler = handler
        self.server: Optional[asyncio.AbstractServer] = None

    async def start(self) -> None:
        """Start listening on the Unix domain socket and set permissions for group 'wheel' access."""
        # Cleanup stale socket file
        if os.path.exists(SOCKET_PATH):
            try:
                os.unlink(SOCKET_PATH)
            except Exception as e:
                logger.error(f"Failed to remove stale socket: {e}")
                
        self.server = await asyncio.start_unix_server(self.handle_connection, SOCKET_PATH)
        logger.info(f"IPC Server listening on Unix socket: {SOCKET_PATH}")
        
        # Change permissions to owner root, group wheel (0660) so dusk can access without sudo
        try:
            # Find GID for wheel
            wheel_gid = grp.getgrnam("wheel").gr_gid
            os.chown(SOCKET_PATH, 0, wheel_gid)
            os.chmod(SOCKET_PATH, 0o660)
            logger.info("IPC Socket permissions updated: owner root:wheel, mode 0660.")
        except KeyError:
            logger.warning("Group 'wheel' not found on system. Defaulting socket permissions.")
            os.chmod(SOCKET_PATH, 0o666)
        except Exception as e:
            logger.error(f"Failed to set socket permissions: {e}")

    async def stop(self) -> None:
        """Shutdown the IPC server and remove the socket file."""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        if os.path.exists(SOCKET_PATH):
            try:
                os.unlink(SOCKET_PATH)
            except Exception:
                pass
        logger.info("IPC Server stopped.")

    async def handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Process incoming RPC messages."""
        try:
            while True:
                data = await reader.readline()
                if not data:
                    break
                
                try:
                    request = json.loads(data.decode("utf-8").strip())
                    response = await self.handler(request)
                except json.JSONDecodeError:
                    response = {"status": "error", "error": "Invalid JSON formatting"}
                except Exception as e:
                    response = {"status": "error", "error": f"Internal execution error: {str(e)}"}
                    
                writer.write((json.dumps(response) + "\n").encode("utf-8"))
                await writer.drain()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"IPC connection exception: {e}")
        finally:
            writer.close()
            await writer.wait_closed()

class IPCClient:
    """Asynchronous client for sending commands over the Unix domain socket."""
    
    async def send_request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Send an RPC request to the daemon and await its response."""
        if not os.path.exists(SOCKET_PATH):
            return {"status": "error", "error": f"Dusky Network Limiter daemon is not running (socket {SOCKET_PATH} not found)."}
            
        try:
            reader, writer = await asyncio.open_unix_connection(SOCKET_PATH)
        except Exception as e:
            return {"status": "error", "error": f"Failed to connect to daemon socket: {e}"}
            
        try:
            payload = {
                "method": method,
                "params": params or {}
            }
            writer.write((json.dumps(payload) + "\n").encode("utf-8"))
            await writer.drain()
            
            response_data = await reader.readline()
            if not response_data:
                return {"status": "error", "error": "No response received from daemon"}
                
            return json.loads(response_data.decode("utf-8").strip())
        except Exception as e:
            return {"status": "error", "error": f"IPC transmission failed: {e}"}
        finally:
            writer.close()
            await writer.wait_closed()
