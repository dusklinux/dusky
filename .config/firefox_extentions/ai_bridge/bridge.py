#!/usr/bin/env python3
# Local AI Web Bridge Daemon & CLI Client - Zero External Dependencies (Standard Library Only)
# Bridges terminal commands to open web AI tabs (ChatGPT, Claude, Gemini, Meta AI) in Firefox.

import sys
import os
import socket
import time as _time
import json
import asyncio
import base64
import hashlib
import uuid
import struct
import subprocess
import threading
import argparse
import mimetypes
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8765

CONNECTED_CLIENTS = set()
PENDING_QUERY_TARGETS = {}

def fast_xor_mask(data: bytes, mask: bytes) -> bytes:
    """Unmask RFC 6455 payload ~25x faster in CPython 3.15 using C-level bigint XOR."""
    n = len(data)
    if not n or not mask:
        return data
    full_mask = (mask * ((n >> 2) + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(full_mask, "big")).to_bytes(n, "big")

def make_ws_response_key(sec_key):
    magic = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    sha1 = hashlib.sha1(
        (sec_key + magic).encode("utf-8"),
        usedforsecurity=False,
    ).digest()
    return base64.b64encode(sha1).decode("utf-8")

class WSConnection:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer

    async def send(self, message):
        data = message.encode('utf-8') if isinstance(message, str) else message
        length = len(data)
        frame = bytearray([0x81])
        if length <= 125:
            frame.append(length)
        elif length <= 65535:
            frame.append(126)
            frame.extend(struct.pack("!H", length))
        else:
            frame.append(127)
            frame.extend(struct.pack("!Q", length))
        frame.extend(data)
        self.writer.write(frame)
        await self.writer.drain()

    async def recv(self):
        fragments = bytearray()
        while True:
            try:
                head = await self.reader.readexactly(2)
            except Exception:
                return None

            b1, b2 = head[0], head[1]
            fin = bool(b1 & 0x80)
            opcode = b1 & 0x0F
            masked = bool(b2 & 0x80)
            payload_len = b2 & 0x7F

            if payload_len == 126:
                data = await self.reader.readexactly(2)
                payload_len = struct.unpack("!H", data)[0]
            elif payload_len == 127:
                data = await self.reader.readexactly(8)
                payload_len = struct.unpack("!Q", data)[0]

            masks = await self.reader.readexactly(4) if masked else None
            raw = await self.reader.readexactly(payload_len)

            if masked and masks:
                raw = fast_xor_mask(raw, masks)

            # Close frame (0x8)
            if opcode == 0x8:
                return None

            # Ping frame (0x9) -> Send Pong (0x8A) and continue loop
            if opcode == 0x9:
                frame = bytearray([0x8A])
                n = len(raw)
                if n <= 125:
                    frame.append(n)
                elif n <= 65535:
                    frame.append(126)
                    frame.extend(struct.pack("!H", n))
                else:
                    frame.append(127)
                    frame.extend(struct.pack("!Q", n))
                frame.extend(raw)
                self.writer.write(frame)
                await self.writer.drain()
                continue

            # Pong frame (0xA)
            if opcode == 0xA:
                continue

            if opcode in (0x1, 0x2, 0x0):
                fragments.extend(raw)
                if fin:
                    return fragments.decode("utf-8", errors="replace")

    def close(self):
        try: self.writer.close()
        except Exception: pass

# --- Hardware Keypress Submission (daemon-level) ---
LAST_NON_FIREFOX_TARGET = {"address": None, "workspace_id": None, "special_ws": None}
SUBMISSION_EVENTS = {}  # requestId -> asyncio.Event

def _ensure_wayland_hypr_env():
    """Ensure WAYLAND_DISPLAY and HYPRLAND_INSTANCE_SIGNATURE are populated under systemd --user."""
    uid = os.getuid()
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
    os.environ.setdefault("XDG_RUNTIME_DIR", runtime_dir)

    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        hypr_dir = Path(runtime_dir) / "hypr"
        if hypr_dir.is_dir():
            candidates = sorted(
                [d for d in hypr_dir.iterdir() if d.is_dir() and (d / ".socket.sock").exists()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if candidates:
                os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = candidates[0].name

    if not os.environ.get("WAYLAND_DISPLAY"):
        for sock_path in sorted(Path(runtime_dir).glob("wayland-*")):
            if not sock_path.name.endswith(".lock"):
                os.environ["WAYLAND_DISPLAY"] = sock_path.name
                break

def _hypr_focus_window(addr):
    if not addr:
        return
    _ensure_wayland_hypr_env()
    # Try native Hyprland dispatcher first, then fallback to Lua hl.dispatch
    res = subprocess.run(
        ["hyprctl", "dispatch", "focuswindow", f"address:{addr}"],
         capture_output=True, text=True, timeout=2,
    )
    if res.returncode != 0 or "ok" not in (res.stdout or "").lower():
        lua_code = f'''
for _, w in ipairs(hl.get_windows()) do
    if w.address == "{addr}" then
        hl.dispatch(hl.dsp.focus({{ window = w }}))
        break
    end
end
'''
        subprocess.run(["hyprctl", "eval", lua_code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)

def get_active_window():
    """Retrieve active window/workspace/special_workspace, caching non-Firefox originating context."""
    _ensure_wayland_hypr_env()
    try:
        special_ws = None
        mon_res = subprocess.run(
            ["hyprctl", "monitors", "-j"],
            capture_output=True, text=True, timeout=2,
        )
        if mon_res.returncode == 0 and mon_res.stdout.strip():
            for m in json.loads(mon_res.stdout):
                sp_name = (m.get("specialWorkspace") or {}).get("name", "")
                if sp_name.startswith("special:"):
                    special_ws = sp_name.split("special:", 1)[1]
                    break

        active_res = subprocess.run(
            ["hyprctl", "activewindow", "-j"],
            capture_output=True, text=True, timeout=2,
        )
        if active_res.returncode == 0 and active_res.stdout.strip():
            win = json.loads(active_res.stdout)
            cls = (win.get("class") or "").lower()
            addr = win.get("address")
            ws_info = win.get("workspace", {}) or {}
            ws_id = ws_info.get("id")
            ws_name = ws_info.get("name", "")
            if not special_ws and ws_name.startswith("special:"):
                special_ws = ws_name.split("special:", 1)[1]

            if addr and "firefox" not in cls:
                LAST_NON_FIREFOX_TARGET["address"] = addr
                LAST_NON_FIREFOX_TARGET["workspace_id"] = ws_id
                LAST_NON_FIREFOX_TARGET["special_ws"] = special_ws
                return addr, ws_id, special_ws
            elif LAST_NON_FIREFOX_TARGET["address"]:
                return (
                    LAST_NON_FIREFOX_TARGET["address"],
                    LAST_NON_FIREFOX_TARGET["workspace_id"],
                    LAST_NON_FIREFOX_TARGET["special_ws"],
                )
            else:
                return addr, ws_id, special_ws
    except Exception:
        pass
    return (
        LAST_NON_FIREFOX_TARGET["address"],
        LAST_NON_FIREFOX_TARGET["workspace_id"],
        LAST_NON_FIREFOX_TARGET["special_ws"],
    )

def _get_firefox_target():
    _ensure_wayland_hypr_env()
    try:
        res = subprocess.run(
            ["hyprctl", "clients", "-j"],
            capture_output=True, text=True, timeout=2,
        )
        if res.returncode != 0:
            return None
        clients = json.loads(res.stdout)
        ai_keywords = (
            "google gemini", "gemini.google", "chatgpt", "claude",
            "meta ai", "chatgpt.com", "claude.ai",
        )
        ff = [c for c in clients if "firefox" in (c.get("class") or "").lower()]
        preferred = None
        for c in ff:
            title = (c.get("title") or "").lower()
            if any(k in title for k in ai_keywords):
                preferred = c
                break
        return preferred or (ff[0] if ff else None)
    except Exception:
        return None

def _wait_for_firefox_focus(expected_addr, max_wait=0.8):
    """Poll hyprctl activewindow until Firefox holds Wayland keyboard focus."""
    deadline = _time.time() + max_wait
    while _time.time() < deadline:
        try:
            res = subprocess.run(
                ["hyprctl", "activewindow", "-j"],
                capture_output=True, text=True, timeout=1,
            )
            if res.returncode == 0 and res.stdout.strip():
                win = json.loads(res.stdout)
                cls = (win.get("class") or "").lower()
                addr = (win.get("address") or "").lower()
                if "firefox" in cls or (expected_addr and addr == expected_addr.lower()):
                    return True
        except Exception:
            pass
        _time.sleep(0.04)
    return False

def _focus_window(target):
    if not target:
        return
    addr = target.get("address")
    ws_info = target.get("workspace", {}) or {}
    ws_id = ws_info.get("id")
    multi_mon_ws = os.environ.get(
        "AI_BRIDGE_MULTI_MON_WS",
        os.path.expanduser("~/user_scripts/hypr/multi_monitor_workspace.sh"),
    )
    if ws_id is not None and ws_id > 0:
        rel_ws = ((ws_id - 1) % 10) + 1
        try:
            subprocess.run(
                [multi_mon_ws, "workspace", str(rel_ws)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
            )
        except Exception:
            pass
    if addr:
        _hypr_focus_window(addr)

async def _do_os_return_async(req_id=None, prev_addr=None, prev_ws_id=None, prev_special=None):
    """Focus Firefox AI tab, verify Wayland focus, send wtype Return, wait for submission confirmation, then restore focus."""
    try:
        if not prev_addr:
            prev_addr, prev_ws_id, prev_special = await asyncio.to_thread(get_active_window)

        target = await asyncio.to_thread(_get_firefox_target)
        if not target:
            return

        addr = target["address"]
        multi_mon_ws = os.environ.get(
            "AI_BRIDGE_MULTI_MON_WS",
            os.path.expanduser("~/user_scripts/hypr/multi_monitor_workspace.sh"),
        )

        await asyncio.to_thread(_focus_window, target)
        await asyncio.to_thread(_wait_for_firefox_focus, addr, 0.8)
        await asyncio.sleep(0.18)

        _ensure_wayland_hypr_env()
        await asyncio.to_thread(
            subprocess.run,
            ["wtype", "-k", "Return"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
        )

        # Wait deterministically for SUBMISSION_CONFIRMED / STREAM_CHUNK / THINKING (up to 0.9s)
        ev = SUBMISSION_EVENTS.get(req_id) if req_id else None
        if ev is not None:
            try:
                await asyncio.wait_for(ev.wait(), timeout=0.9)
            except asyncio.TimeoutError:
                pass
        else:
            await asyncio.sleep(0.45)

        if prev_addr and prev_addr.lower() != addr.lower():
            print(f"[Daemon] Restoring focus: ws={prev_ws_id} sp={prev_special} addr={prev_addr}", flush=True)
            def _restore():
                if prev_special:
                    subprocess.run(
                        ["hyprctl", "dispatch", f'hl.dsp.workspace.toggle_special("{prev_special}")'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
                    )
                elif prev_ws_id is not None and prev_ws_id > 0:
                    rel_ws = ((prev_ws_id - 1) % 10) + 1
                    try:
                        subprocess.run(
                            [multi_mon_ws, "workspace", str(rel_ws)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
                        )
                    except Exception:
                        pass
                _hypr_focus_window(prev_addr)
            await asyncio.to_thread(_restore)
            print("[Daemon] Focus restored OK", flush=True)
        else:
            print(f"[Daemon] Skipping focus restore: prev_addr={prev_addr} ff_addr={addr}", flush=True)
    except Exception as e:
        print(f"[Daemon] _do_os_return_async error: {e!r}", flush=True)
    finally:
        if req_id:
            SUBMISSION_EVENTS.pop(req_id, None)

async def handle_client(reader, writer):
    headers = ""
    while "\r\n\r\n" not in headers:
        line = await reader.readline()
        if not line: break
        headers += line.decode('utf-8', errors='replace')

    sec_key = None
    for l in headers.split("\r\n"):
        if l.lower().startswith("sec-websocket-key:"):
            sec_key = l.split(":", 1)[1].strip()
            break

    if not sec_key:
        resp = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n{\"status\":\"ok\"}\n"
        writer.write(resp.encode('utf-8'))
        await writer.drain()
        try: writer.close()
        except Exception: pass
        return

    resp_key = make_ws_response_key(sec_key)
    handshake = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {resp_key}\r\n\r\n"
    )
    writer.write(handshake.encode('utf-8'))
    await writer.drain()

    ws = WSConnection(reader, writer)
    CONNECTED_CLIENTS.add(ws)
    print(f"[Daemon] Client connected. Active clients: {len(CONNECTED_CLIENTS)}")

    try:
        while True:
            msg = await ws.recv()
            if msg is None: break

            try: data = json.loads(msg)
            except Exception: continue

            msg_type = data.get("type")
            req_id = data.get("requestId")
            print(f"[Daemon] MSG: {msg_type} from ws={id(ws)} clients={len(CONNECTED_CLIENTS)}", flush=True)

            if msg_type in ("PING", "HEARTBEAT"):
                await ws.send(json.dumps({"type": "PONG"}))
                continue

            if msg_type == "DIAGNOSE_TABS":
                dead = set()
                for c in CONNECTED_CLIENTS:
                    if c != ws:
                        try: await c.send(msg)
                        except Exception: dead.add(c)
                CONNECTED_CLIENTS.difference_update(dead)
                continue

            if msg_type == "TAB_INFO":
                info_req = data.get("requestId")
                target_url = data.get("url", "")
                print(f"[Daemon] Target tab confirmed ({target_url}) for {info_req} — waiting for JS submission or NEED_OS_RETURN", flush=True)
                continue

            if msg_type == "NEED_OS_RETURN":
                prev_info = PENDING_QUERY_TARGETS.pop(req_id, (None, None, None))
                print(f"[Daemon] NEED_OS_RETURN requested by tab for {req_id} — executing _do_os_return_async now", flush=True)
                asyncio.create_task(_do_os_return_async(req_id, prev_info[0], prev_info[1], prev_info[2]))
                continue

            if msg_type in ("SUBMISSION_CONFIRMED", "THINKING", "STREAM_CHUNK", "FINAL", "STOP_QUERY"):
                if req_id:
                    PENDING_QUERY_TARGETS.pop(req_id, None)
                    ev = SUBMISSION_EVENTS.get(req_id)
                    if ev is not None:
                        ev.set()
                if msg_type == "SUBMISSION_CONFIRMED":
                    continue

            # Record caller's active window/workspace at T=0.0s asynchronously off the event loop
            prev_addr, prev_ws_id, prev_special = None, None, None
            if msg_type == "RUN_QUERY":
                prev_addr, prev_ws_id, prev_special = await asyncio.to_thread(get_active_window)
                if req_id:
                    PENDING_QUERY_TARGETS[req_id] = (prev_addr, prev_ws_id, prev_special)
                    SUBMISSION_EVENTS[req_id] = asyncio.Event()

            # Broadcast message to all other connected clients
            dead = set()
            broadcast_count = 0
            for c in CONNECTED_CLIENTS:
                if c != ws:
                    try:
                        await c.send(msg)
                        broadcast_count += 1
                    except Exception:
                        dead.add(c)
            CONNECTED_CLIENTS.difference_update(dead)

            if msg_type == "RUN_QUERY":
                query_preview = data.get("query", "")[:60]
                print(f"[Daemon] RUN_QUERY broadcast to {broadcast_count} client(s): '{query_preview}...'", flush=True)

                if broadcast_count == 0:
                    print("[Daemon] No extension clients — waiting up to 8s for reconnect...", flush=True)
                    for _retry in range(16):
                        await asyncio.sleep(0.5)
                        others = [c for c in CONNECTED_CLIENTS if c != ws]
                        if others:
                            dead = set()
                            broadcast_count = 0
                            for c in others:
                                try:
                                    await c.send(msg)
                                    broadcast_count += 1
                                except Exception:
                                    dead.add(c)
                            CONNECTED_CLIENTS.difference_update(dead)
                            if broadcast_count > 0:
                                print(f"[Daemon] Retry succeeded — broadcast to {broadcast_count} client(s)", flush=True)
                                break
                    if broadcast_count == 0:
                        print("[Daemon] WARNING: No extension clients after 8s — query lost!", flush=True)
                        try:
                            await ws.send(json.dumps({
                                "type": "ERROR",
                                "error": "No browser extension connected — is Firefox running with the extension loaded?",
                                "requestId": req_id,
                            }))
                        except Exception:
                            pass

                # Note: Hardware Return is now driven deterministically by NEED_OS_RETURN from content_main.js

    except Exception as e:
        print(f"[Daemon] client handler error: {e!r}", flush=True)
    finally:
        CONNECTED_CLIENTS.discard(ws)
        ws.close()
        print(f"[Daemon] Client disconnected. Active clients: {len(CONNECTED_CLIENTS)}")

SERVICE_NAME = "localai_bridge.service"

async def start_daemon():
    try:
        test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        test_sock.settimeout(0.5)
        test_sock.connect((HOST, PORT))
        test_sock.close()
        print(f"[Daemon] Port {PORT} in use — stopping stale daemon...", flush=True)

        svc_check = subprocess.run(
            ["systemctl", "--user", "is-active", SERVICE_NAME],
            capture_output=True, text=True, timeout=3,
        )
        if svc_check.stdout.strip() == "active":
            print(f"[Daemon] Stopping {SERVICE_NAME} via systemctl", flush=True)
            subprocess.run(
                ["systemctl", "--user", "stop", SERVICE_NAME],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
            )
            _time.sleep(0.3)
        else:
            import re as _re
            res = subprocess.run(
                ["ss", "-tlnp", f"sport = :{PORT}"],
                capture_output=True, text=True, timeout=2,
            )
            for line in res.stdout.splitlines():
                if f":{PORT}" in line:
                    m = _re.search(r'pid=(\d+)', line)
                    if m:
                        old_pid = int(m.group(1))
                        if old_pid != os.getpid():
                            print(f"[Daemon] Killing stale PID {old_pid}", flush=True)
                            os.kill(old_pid, 15)
                            _time.sleep(0.5)
    except (ConnectionRefusedError, OSError):
        pass

    print(f"[Daemon] Starting Local AI WebSocket Daemon on {HOST}:{PORT} (Standard Library)")
    server = await asyncio.start_server(
        handle_client, HOST, PORT,
        reuse_address=True,
    )
    async with server:
        await server.serve_forever()

def safe_write(text):
    if not text: return
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except UnicodeEncodeError:
        try:
            clean = text.encode('utf-16', 'surrogatepass').decode('utf-16', 'replace')
            sys.stdout.write(clean)
            sys.stdout.flush()
        except Exception:
            pass

def is_port_open(host=HOST, port=PORT, timeout=0.3):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as test_sock:
            test_sock.settimeout(timeout)
            test_sock.connect((host, port))
            return True
    except (ConnectionRefusedError, OSError):
        return False

def ensure_daemon():
    if is_port_open():
        return True

    try:
        subprocess.run(
            ["systemctl", "--user", "restart", SERVICE_NAME],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3,
        )
        for _ in range(15):
            _time.sleep(0.2)
            if is_port_open():
                return True
    except Exception:
        pass

    import subprocess as _sp
    cmd = [sys.executable, os.path.abspath(__file__), "--daemon"]
    _sp.Popen(cmd, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, start_new_session=True)
    for _ in range(20):
        _time.sleep(0.1)
        if is_port_open():
            return True
    return False

def _ws_send_masked_json(sock, obj):
    payload = json.dumps(obj).encode("utf-8")
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    header = bytearray([0x81])
    n = len(payload)
    if n <= 125:
        header.append(0x80 | n)
    elif n <= 65535:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", n))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", n))
    sock.sendall(header + mask + masked)

def prepare_file_attachments(file_paths):
    attachments = []
    for raw_path in file_paths or []:
        p = Path(raw_path).expanduser().resolve()
        if not p.is_file():
            print(f"[Error] Attachment file not found: {raw_path}", file=sys.stderr)
            sys.exit(1)
        mime, _ = mimetypes.guess_type(str(p))
        if not mime:
            if p.suffix.lower() in (".py", ".rs", ".c", ".cpp", ".h", ".js", ".ts", ".tsx", ".jsx", ".sh", ".md", ".json", ".toml", ".yaml", ".yml", ".ini", ".conf", ".lua"):
                mime = "text/plain"
            else:
                mime = "application/octet-stream"
        data_b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        attachments.append({
            "name": p.name,
            "mime": mime,
            "data": data_b64,
        })
    return attachments

def send_query_cli(
    query_text,
    files=None,
    stream=True,
    idle_timeout=60.0,
    hard_timeout=1800.0,
    trip_enabled=True,
    trip_threshold=5,
    trip_leading=False,
    model=None,
    tab_id=None,
):
    if not is_port_open(HOST, PORT):
        if not ensure_daemon():
            print(f"[Error] Local AI Bridge daemon on {HOST}:{PORT} could not be started.", file=sys.stderr)
            sys.exit(1)

    s = socket.socket()
    s.settimeout(1.0)
    try:
        s.connect((HOST, PORT))
    except (ConnectionRefusedError, OSError) as e:
        print(f"[Error] Failed to connect to Local AI Bridge on {HOST}:{PORT}: {e}", file=sys.stderr)
        sys.exit(1)

    sec_key = base64.b64encode(os.urandom(16)).decode("utf-8")
    handshake = (
        f"GET / HTTP/1.1\r\n"
        f"Host: {HOST}:{PORT}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {sec_key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n\r\n"
    )
    s.sendall(handshake.encode("utf-8"))

    resp = b""
    while b"\r\n\r\n" not in resp:
        try:
            chunk = s.recv(1024)
        except socket.timeout:
            continue
        if not chunk:
            break
        resp += chunk
        if len(resp) > 65536:
            break

    if b"101" not in resp.split(b"\r\n", 1)[0]:
        print("[Error] WebSocket handshake failed:", resp[:200], file=sys.stderr)
        try: s.close()
        except Exception: pass
        sys.exit(1)

    header_end = resp.find(b"\r\n\r\n") + 4
    rx_buf = bytearray(resp[header_end:])

    req_id = str(uuid.uuid4())
    req_payload = {
        "type": "RUN_QUERY",
        "query": query_text,
        "files": files or [],
        "model": model,
        "requestId": req_id,
        "tripEnabled": trip_enabled,
        "tripThreshold": trip_threshold,
        "tripLeading": trip_leading,
        "tripTrailing": trip_enabled,
    }
    if tab_id is not None:
        req_payload["tabId"] = tab_id
    _ws_send_masked_json(s, req_payload)

    full_text = ""
    emitted = ""
    started = _time.time()
    last_chunk_at = None
    thinking_shown = False

    def emit_remainder(text):
        nonlocal emitted
        if not stream or not text:
            return
        if text.startswith(emitted):
            delta = text[len(emitted):]
            if delta:
                safe_write(delta)
            emitted = text
            return

        norm_emitted = "".join(emitted.split())
        norm_text = "".join(text.split())
        if norm_text.startswith(norm_emitted):
            e_idx = 0
            t_idx = 0
            while e_idx < len(emitted) and t_idx < len(text):
                if emitted[e_idx] == text[t_idx]:
                    e_idx += 1
                    t_idx += 1
                elif text[t_idx].isspace():
                    t_idx += 1
                elif emitted[e_idx].isspace():
                    e_idx += 1
                else:
                    break
            if t_idx < len(text):
                safe_write(text[t_idx:])
            emitted = text
            return

        safe_write(("\n" if emitted else "") + text)
        emitted = text

    def finish(text, note=None):
        if stream:
            emit_remainder(text or "")
            sys.stdout.write("\n")
            sys.stdout.flush()
        else:
            print(text or "")
        if note:
            print(f"[bridge] {note}", file=sys.stderr)
        try:
            s.close()
        except Exception:
            pass
        return text or ""

    def fill_rx_buf(min_bytes):
        while len(rx_buf) < min_bytes:
            try:
                part = s.recv(max(4096, min_bytes - len(rx_buf)))
            except socket.timeout:
                return False
            if not part:
                return None
            rx_buf.extend(part)
        return True

    def try_read_ws_frame():
        status = fill_rx_buf(2)
        if not status:
            return status

        b1, b2 = rx_buf[0], rx_buf[1]
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        l = b2 & 0x7F
        offset = 2

        if l == 126:
            status = fill_rx_buf(offset + 2)
            if not status:
                return status
            l = struct.unpack("!H", rx_buf[offset : offset + 2])[0]
            offset += 2
        elif l == 127:
            status = fill_rx_buf(offset + 8)
            if not status:
                return status
            l = struct.unpack("!Q", rx_buf[offset : offset + 8])[0]
            offset += 8

        mask_keys = None
        if masked:
            status = fill_rx_buf(offset + 4)
            if not status:
                return status
            mask_keys = bytes(rx_buf[offset : offset + 4])
            offset += 4

        status = fill_rx_buf(offset + l)
        if not status:
            return status

        payload = bytes(rx_buf[offset : offset + l])
        del rx_buf[: offset + l]

        if mask_keys is not None:
            payload = fast_xor_mask(payload, mask_keys)

        fin = bool(b1 & 0x80)
        return (fin, opcode, payload)

    frag_buf = bytearray()
    try:
        while True:
            now = _time.time()
            if now - started > hard_timeout:
                if full_text:
                    return finish(full_text, f"hard timeout after {hard_timeout:.0f}s — returning partial")
                print(f"\n[Error] Timed out after {hard_timeout:.0f}s with no response", file=sys.stderr)
                try: s.close()
                except Exception: pass
                sys.exit(1)

            if last_chunk_at is not None and full_text and (now - last_chunk_at) >= idle_timeout:
                return finish(full_text, f"idle timeout ({idle_timeout:.0f}s) — treating as FINAL")

            frame_res = try_read_ws_frame()
            if frame_res is False:
                continue
            if frame_res is None:
                break

            fin, opcode, p = frame_res
            if opcode == 0x8:
                break
            if opcode == 0x9:
                # Respond to Ping with masked Pong
                mask = os.urandom(4)
                masked_pong = fast_xor_mask(p[:125], mask)
                s.sendall(bytes([0x8A, 0x80 | len(masked_pong)]) + mask + masked_pong)
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x2, 0x0):
                frag_buf.extend(p)
                if not fin:
                    continue
                p = bytes(frag_buf)
                frag_buf.clear()
            else:
                continue

            try:
                data = json.loads(p.decode("utf-8"))
            except Exception:
                continue

            if data.get("requestId") != req_id:
                continue

            mtype = data.get("type")
            if mtype == "STREAM_CHUNK":
                chunk = data.get("text", "")
                new_full = data.get("full")
                if new_full:
                    full_text = new_full
                elif chunk:
                    full_text = full_text + chunk

                if stream:
                    if new_full is not None:
                        emit_remainder(full_text)
                    elif chunk:
                        safe_write(chunk)
                        emitted += chunk

                last_chunk_at = _time.time()
            elif mtype == "THINKING":
                last_chunk_at = _time.time()
                if not thinking_shown:
                    print("[Thinking...]", file=sys.stderr, flush=True)
                    thinking_shown = True
            elif mtype == "FINAL":
                full = data.get("full", full_text) or full_text
                reason = data.get("reason", "")
                if reason == "interrupted":
                    finish(full, note=None)
                    print("[Interrupted by new query]", file=sys.stderr, flush=True)
                    sys.exit(2)
                if reason == "token-saver-trip":
                    return finish(full, note="Token-Saver tripped: halted server generation automatically")
                if reason == "user-stopped":
                    return finish(full, note="Generation stopped by user")
                return finish(full)
            elif mtype == "ERROR":
                print(f"\n[Error] {data.get('error')}", file=sys.stderr)
                try: s.close()
                except Exception: pass
                sys.exit(1)

    except KeyboardInterrupt:
        print("\n[bridge] Ctrl+C detected — sending STOP_QUERY to halt browser token generation...", file=sys.stderr, flush=True)
        try:
            _ws_send_masked_json(s, {"type": "STOP_QUERY", "requestId": req_id})
            _time.sleep(0.25)
        except Exception:
            pass
        try:
            s.close()
        except Exception:
            pass
        sys.exit(130)

    if full_text:
        return finish(full_text, "connection closed — returning partial")
    print("\n[Error] Connection closed with no response", file=sys.stderr)
    sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local AI Web Bridge Daemon & CLI")
    parser.add_argument("--daemon", action="store_true", help="Run WebSocket bridge daemon")
    parser.add_argument("-m", "--model", type=str, default=None, help="Target AI model tab (chatgpt, gemini, claude, meta)")
    parser.add_argument("-f", "--file", action="append", default=[], help="Attach file(s) to the prompt")
    parser.add_argument("-i", "--image", action="append", default=[], help="Attach image(s) to the prompt")
    parser.add_argument("--no-trip", action="store_true", help="Disable Token-Saver automatic sequence trip")
    parser.add_argument("--trip-leading", action="store_true", help="Trip and stop immediately on leading 1..500 countdowns")
    parser.add_argument("--trip-threshold", type=int, default=5, help="Sequential integer count to trigger trip (default: 5)")
    parser.add_argument("--idle-timeout", type=float, default=60.0, help="Idle timeout in seconds before treating as FINAL (default: 60.0)")
    parser.add_argument("--tab-id", type=int, default=None, help="Target specific browser tab ID")
    parser.add_argument("query", nargs="*", help="Prompt query string")
    args = parser.parse_args()

    if args.daemon:
        asyncio.run(start_daemon())
    else:
        all_files = (args.file or []) + (args.image or [])
        query_str = " ".join(args.query).strip()
        if not query_str and not sys.stdin.isatty():
            query_str = sys.stdin.read().strip()
        if not query_str and not all_files:
            parser.print_help()
            sys.exit(1)
        attachments = prepare_file_attachments(all_files)
        send_query_cli(
            query_str,
            files=attachments,
            idle_timeout=args.idle_timeout,
            trip_enabled=not args.no_trip,
            trip_threshold=args.trip_threshold,
            trip_leading=args.trip_leading,
            model=args.model,
            tab_id=args.tab_id,
        )
