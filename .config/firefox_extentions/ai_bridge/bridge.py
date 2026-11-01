#!/usr/bin/env python3
"""
Local AI Web Bridge Daemon & CLI Client - Zero External Dependencies (Standard Library Only)
Bridges terminal commands to open web AI tabs (ChatGPT, Claude, Gemini, Meta AI) in Firefox.
"""

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

HOST = "127.0.0.1"
PORT = 8765

CONNECTED_CLIENTS = set()
PENDING_QUERY_TARGETS = {}

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
        try:
            head = await self.reader.readexactly(2)
        except Exception:
            return None

        b1, b2 = head[0], head[1]
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

        if masked:
            raw = bytes(b ^ masks[i % 4] for i, b in enumerate(raw))

        # Close frame (0x8)
        if opcode == 0x8:
            return None

        # Ping frame (0x9) -> Send Pong (0x8A)
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
            return await self.recv()

        # Pong frame (0xA)
        if opcode == 0xA:
            return await self.recv()

        # Only process text/continuation frames
        if opcode not in (0x1, 0x0):
            return await self.recv()

        return raw.decode("utf-8", errors="replace")

    def close(self):
        try: self.writer.close()
        except: pass

# --- Hardware Keypress Submission (daemon-level) ---
# Gemini ignores synthetic JS clicks (isTrusted:false). The ONLY reliable
# way to submit a typed prompt is via an OS-level hardware keypress.
# This function is called by the daemon for EVERY RUN_QUERY, regardless
# of whether the caller is bridge.py CLI, a benchmark script, or the SDK.
import time as _time

LAST_NON_FIREFOX_TARGET = {"address": None, "workspace_id": None}

def get_active_window():
    """Retrieve active window/workspace, automatically caching non-Firefox originating context across rapid queries."""
    try:
        active_res = subprocess.run(
            ["hyprctl", "activewindow", "-j"],
            capture_output=True, text=True, timeout=2,
        )
        if active_res.returncode == 0 and active_res.stdout.strip():
            win = json.loads(active_res.stdout)
            cls = (win.get("class") or "").lower()
            addr = win.get("address")
            ws_id = win.get("workspace", {}).get("id")
            if addr and "firefox" not in cls:
                LAST_NON_FIREFOX_TARGET["address"] = addr
                LAST_NON_FIREFOX_TARGET["workspace_id"] = ws_id
                return addr, ws_id
            elif LAST_NON_FIREFOX_TARGET["address"]:
                return LAST_NON_FIREFOX_TARGET["address"], LAST_NON_FIREFOX_TARGET["workspace_id"]
            else:
                return addr, ws_id
    except Exception:
        pass
    return LAST_NON_FIREFOX_TARGET["address"], LAST_NON_FIREFOX_TARGET["workspace_id"]

def _get_firefox_target():
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
        subprocess.run(
            ["hyprctl", "dispatch", f'hl.dsp.focus({{ workspace = "{ws_id}" }})'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
        )
    if addr:
        subprocess.run(
            ["hyprctl", "dispatch", f'hl.dsp.focus({{ window = "address:{addr}" }})'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
        )

def _do_os_return(prev_addr=None, prev_ws_id=None):
    """Focus Firefox AI tab, send hardware Return for Gemini, and restore previous window/workspace focus."""
    try:
        if not prev_addr:
            prev_addr, prev_ws_id = get_active_window()

        target = _get_firefox_target()
        if not target:
            return

        addr = target["address"]
        ws_info = target.get("workspace", {}) or {}
        ws_id = ws_info.get("id")
        multi_mon_ws = os.environ.get(
            "AI_BRIDGE_MULTI_MON_WS",
            os.path.expanduser("~/user_scripts/hypr/multi_monitor_workspace.sh"),
        )

        _focus_window(target)

        _time.sleep(0.6)  # Let Firefox fully accept focus before keypress
        # Plain Return only — Ctrl+Return inserts a newline in Gemini's
        # rich-textarea, which prevents the subsequent Return from submitting.
        subprocess.run(
            ["wtype", "-k", "Return"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
        )

        # 3. Restore focus back to starting window & workspace!
        if prev_addr and prev_addr.lower() != addr.lower():
            _time.sleep(0.5)  # Let wtype Return register before switching away
            print(f"[Daemon] Restoring focus: ws={prev_ws_id} addr={prev_addr}", flush=True)
            if prev_ws_id is not None and prev_ws_id > 0:
                rel_ws = ((prev_ws_id - 1) % 10) + 1
                try:
                    subprocess.run(
                        [multi_mon_ws, "workspace", str(rel_ws)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
                    )
                except Exception:
                    pass
                subprocess.run(
                    ["hyprctl", "dispatch", f'hl.dsp.focus({{ workspace = "{prev_ws_id}" }})'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
                )
            r = subprocess.run(
                ["hyprctl", "dispatch", f'hl.dsp.focus({{ window = "address:{prev_addr}" }})'],
                capture_output=True, text=True, timeout=2,
            )
            if r.returncode != 0:
                print(f"[Daemon] Focus restore failed: {r.stderr.strip()}", flush=True)
            else:
                print(f"[Daemon] Focus restored OK", flush=True)
        else:
            print(f"[Daemon] Skipping focus restore: prev_addr={prev_addr} ff_addr={addr} same={prev_addr and prev_addr.lower() == addr.lower()}", flush=True)
    except Exception as e:
        print(f"[Daemon] _do_os_return error: {e!r}", flush=True)

_pending_os_return = {"task": None, "timer": None}  # Debounce state

def _schedule_os_return(prev_addr=None, prev_ws_id=None):
    """Schedule hardware Return keypress & focus restoration 1.5s after query dispatch.
    
    Debounced: if called again before the previous schedule fires, the old one
    is cancelled. This prevents multiple wtype Return keystrokes from rapid queries.
    """
    # Cancel any pending schedule
    if _pending_os_return["timer"] is not None:
        _pending_os_return["timer"].cancel()
        _pending_os_return["timer"] = None
        print("[Daemon] Cancelled pending _do_os_return (debounce)", flush=True)
    if _pending_os_return["task"] is not None:
        _pending_os_return["task"].cancel()
        _pending_os_return["task"] = None
        print("[Daemon] Cancelled pending _do_os_return task (debounce)", flush=True)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        t = threading.Timer(1.5, _do_os_return, args=(prev_addr, prev_ws_id))
        t.daemon = True
        t.start()
        _pending_os_return["timer"] = t
        return

    async def _run():
        await asyncio.sleep(1.5)
        await asyncio.to_thread(_do_os_return, prev_addr, prev_ws_id)

    task = loop.create_task(_run())
    _pending_os_return["task"] = task

    def _consume(done):
        if _pending_os_return["task"] is done:
            _pending_os_return["task"] = None
        if done.cancelled():
            return
        exc = done.exception()
        if exc is not None:
            print(f"[Daemon] _do_os_return task error: {exc!r}", flush=True)

    task.add_done_callback(_consume)

async def handle_client(reader, writer):
    # Perform HTTP WebSocket Handshake
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
        except: pass
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

            if msg_type in ("PING", "HEARTBEAT"):
                await ws.send(json.dumps({"type": "PONG"}))
                continue

            if msg_type == "DIAGNOSE_TABS":
                # Forward to background.js and relay response
                dead = set()
                for c in CONNECTED_CLIENTS:
                    if c != ws:
                        try: await c.send(msg)
                        except Exception: dead.add(c)
                CONNECTED_CLIENTS.difference_update(dead)
                continue

            if msg_type == "TAB_INFO":
                info_req = data.get("requestId")
                is_gem = data.get("isGemini", False)
                prev_info = PENDING_QUERY_TARGETS.pop(info_req, (None, None))
                if is_gem:
                    print(f"[Daemon] Gemini target confirmed for {info_req} — scheduling _do_os_return in 1.5s", flush=True)
                    _schedule_os_return(prev_info[0], prev_info[1])
                else:
                    print(f"[Daemon] Non-Gemini target ({data.get('url', '')}) — skipping hardware Return (JS click sufficient)", flush=True)
                continue

            # Capture caller's active window/workspace IMMEDIATELY at T=0.0s and ensure Firefox has Wayland focus for typing
            prev_addr = None
            prev_ws_id = None
            if msg_type == "RUN_QUERY":
                prev_addr, prev_ws_id = get_active_window()
                ff_target = _get_firefox_target()
                if ff_target:
                    _focus_window(ff_target)
                    await asyncio.sleep(0.3)
                if req_id:
                    PENDING_QUERY_TARGETS[req_id] = (prev_addr, prev_ws_id)

            # Broadcast all messages to all other connected clients
            dead = set()
            broadcast_count = 0
            for c in CONNECTED_CLIENTS:
                if c != ws:
                    try:
                        await c.send(msg)
                        broadcast_count += 1
                    except Exception: dead.add(c)
            CONNECTED_CLIENTS.difference_update(dead)

            if msg_type == "RUN_QUERY":
                query_preview = data.get("query", "")[:60]
                print(f"[Daemon] RUN_QUERY broadcast to {broadcast_count} client(s): '{query_preview}...'", flush=True)

                # If no extension clients received the query, wait for one to connect
                # (handles race after daemon restart — extension reconnects in ~2.5s)
                if broadcast_count == 0:
                    print("[Daemon] No extension clients — waiting up to 8s for reconnect...", flush=True)
                    for _retry in range(16):  # 16 x 0.5s = 8s max
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
                        # Notify the CLI client that the query couldn't be delivered
                        try:
                            await ws.send(json.dumps({
                                "type": "ERROR",
                                "error": "No browser extension connected — is Firefox running with the extension loaded?",
                                "requestId": data.get("requestId"),
                            }))
                        except Exception:
                            pass

                # Gemini fallback check in case TAB_INFO is delayed or not received
                async def _gemini_fallback_check(r_id, p_addr, p_ws):
                    await asyncio.sleep(1.6)
                    if r_id in PENDING_QUERY_TARGETS:
                        PENDING_QUERY_TARGETS.pop(r_id, None)
                        try:
                            res = subprocess.run(["hyprctl", "activewindow", "-j"], capture_output=True, text=True, timeout=1)
                            if res.returncode == 0 and "gemini" in res.stdout.lower():
                                print(f"[Daemon] Fallback: active window is Gemini — scheduling _do_os_return", flush=True)
                                _schedule_os_return(p_addr, p_ws)
                        except Exception:
                            pass

                if broadcast_count > 0 and req_id:
                    asyncio.create_task(_gemini_fallback_check(req_id, prev_addr, prev_ws_id))

    except Exception as e:
        print(f"[Daemon] client handler error: {e!r}", flush=True)
    finally:
        CONNECTED_CLIENTS.discard(ws)
        ws.close()
        print(f"[Daemon] Client disconnected. Active clients: {len(CONNECTED_CLIENTS)}")

SERVICE_NAME = "localai_bridge.service"

async def start_daemon():
    # Stop any stale daemon holding the port — systemd-aware
    try:
        test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        test_sock.settimeout(0.5)
        test_sock.connect((HOST, PORT))
        test_sock.close()
        # Port is occupied — figure out who owns it and stop them properly
        print(f"[Daemon] Port {PORT} in use — stopping stale daemon...", flush=True)

        # Check if the systemd user service is running
        svc_check = subprocess.run(
            ["systemctl", "--user", "is-active", SERVICE_NAME],
            capture_output=True, text=True, timeout=3,
        )
        if svc_check.stdout.strip() == "active":
            # Systemd owns it — must use systemctl to stop (Restart=always
            # would just respawn a PID-killed process)
            print(f"[Daemon] Stopping {SERVICE_NAME} via systemctl", flush=True)
            subprocess.run(
                ["systemctl", "--user", "stop", SERVICE_NAME],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
            )
            _time.sleep(0.3)
        else:
            # Not systemd-managed — kill the PID directly
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
                            os.kill(old_pid, 15)  # SIGTERM
                            _time.sleep(0.5)
    except (ConnectionRefusedError, OSError):
        pass  # Port is free

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
    """Ensure the bridge daemon is running, auto-starting via systemctl or background process."""
    if is_port_open(HOST, PORT):
        return True

    # 1. Try systemd user service first
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", SERVICE_NAME],
            capture_output=True, text=True, timeout=2,
        )
        if r.stdout.strip() != "active":
            print(f"[bridge] Starting {SERVICE_NAME}...", file=sys.stderr)
            subprocess.run(
                ["systemctl", "--user", "start", SERVICE_NAME],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
            )
    except Exception:
        pass

    # Wait up to 3s for systemd to bring it up
    t_end = _time.time() + 3.0
    while _time.time() < t_end:
        if is_port_open(HOST, PORT):
            return True
        _time.sleep(0.1)

    # 2. Fallback: launch daemon directly in background
    print("[bridge] Spawning daemon process in background...", file=sys.stderr)
    try:
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--daemon"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        print(f"[bridge] Failed to spawn daemon: {e}", file=sys.stderr)
        return False

    t_end = _time.time() + 3.5
    while _time.time() < t_end:
        if is_port_open(HOST, PORT):
            return True
        _time.sleep(0.1)

    return False

def send_query_cli(query_text, stream=True, idle_timeout=10.0, hard_timeout=1800.0):
    """Send one prompt and wait for FINAL (or idle/hard timeout fallback).

    idle_timeout: seconds without STREAM_CHUNK after first token before treating
                  the last full text as complete (guards against missing FINAL).
    hard_timeout: absolute max wait from query send.
    """
    if not is_port_open(HOST, PORT):
        if not ensure_daemon():
            print(f"[Error] Local AI Bridge daemon on {HOST}:{PORT} could not be started.", file=sys.stderr)
            print("Run 'python3 bridge.py --daemon' or 'systemctl --user start localai_bridge.service'.", file=sys.stderr)
            sys.exit(1)

    s = socket.socket()
    s.settimeout(1.0)  # allow periodic idle checks
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

    # Read full HTTP response headers (do not assume one recv is enough).
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
        try:
            s.close()
        except Exception:
            pass
        sys.exit(1)

    req_id = str(uuid.uuid4())
    payload = json.dumps(
        {"type": "RUN_QUERY", "query": query_text, "requestId": req_id}
    ).encode("utf-8")

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
    s.sendall(header + mask + masked)

    # Hardware Return is scheduled by the daemon for ALL RUN_QUERY messages.

    full_text = ""
    emitted = ""
    started = _time.time()
    last_chunk_at = None
    thinking_shown = False

    def emit_remainder(text):
        """Print only the unseen suffix; handle non-prefix rewrites without tautologies."""
        nonlocal emitted
        if not stream or not text:
            return

        # 1. Exact string prefix check (fast path)
        if text.startswith(emitted):
            delta = text[len(emitted):]
            if delta:
                safe_write(delta)
            emitted = text
            return

        # 2. Whitespace-insensitive prefix check (handles DOM re-renders where newlines/spaces change)
        norm_emitted = "".join(emitted.split())
        norm_text = "".join(text.split())
        if norm_text.startswith(norm_emitted):
            # Same content, DOM just adjusted spacing — find where actual new content begins
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
                delta = text[t_idx:]
                safe_write(delta)
            emitted = text
            return

        # 3. True full rewrite mid-stream: mark a break then print.
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

    def recv_exact(count):
        buf = bytearray()
        while len(buf) < count:
            try:
                part = s.recv(count - len(buf))
            except socket.timeout:
                return None  # signal caller to run idle/hard timeout checks
            if not part:
                return b""
            buf.extend(part)
        return buf.take_bytes() if hasattr(buf, "take_bytes") else bytes(buf)

    while True:
        now = _time.time()
        if now - started > hard_timeout:
            if full_text:
                return finish(full_text, f"hard timeout after {hard_timeout:.0f}s — returning partial")
            print(f"\n[Error] Timed out after {hard_timeout:.0f}s with no response", file=sys.stderr)
            try:
                s.close()
            except Exception:
                pass
            sys.exit(1)

        if last_chunk_at is not None and full_text and (now - last_chunk_at) >= idle_timeout:
            return finish(full_text, f"idle timeout ({idle_timeout:.0f}s) — treating as FINAL")

        head = recv_exact(2)
        if head is None:
            continue
        if head == b"" or len(head) < 2:
            break

        opcode = head[0] & 0x0F
        masked = bool(head[1] & 0x80)
        l = head[1] & 0x7F

        if l == 126:
            raw = recv_exact(2)
            if raw is None:
                continue
            if len(raw) < 2:
                break
            l = struct.unpack("!H", raw)[0]
        elif l == 127:
            raw = recv_exact(8)
            if raw is None:
                continue
            if len(raw) < 8:
                break
            l = struct.unpack("!Q", raw)[0]

        if masked:
            mask_keys = recv_exact(4)
            if mask_keys is None:
                continue
            if len(mask_keys) < 4:
                break
        else:
            mask_keys = None

        p = recv_exact(l)
        if p is None:
            continue
        if len(p) < l:
            break

        if mask_keys is not None:
            p = bytes(b ^ mask_keys[i % 4] for i, b in enumerate(p))

        # Close / ping / pong / non-text
        if opcode == 0x8:
            break
        if opcode in (0x9, 0xA):
            continue
        if opcode != 0x1:
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
            # AI is thinking (reasoning model) — reset idle timer, keep waiting
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
                try: s.close()
                except Exception: pass
                sys.exit(2)
            return finish(full)
        elif mtype == "ERROR":
            print(f"\n[Error] {data.get('error')}", file=sys.stderr)
            try:
                s.close()
            except Exception:
                pass
            sys.exit(1)

    if full_text:
        return finish(full_text, "connection closed — returning partial")
    print("\n[Error] Connection closed with no response", file=sys.stderr)
    sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "--daemon":
            asyncio.run(start_daemon())
        else:
            query = " ".join(sys.argv[1:])
            send_query_cli(query)
    else:
        print("Usage:")
        print("  python3 bridge.py --daemon            # Run server daemon")
        print("  python3 bridge.py \"<your prompt>\"    # Send prompt CLI query")
