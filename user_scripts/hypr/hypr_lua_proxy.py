#!/usr/bin/env python3
"""
hypr_lua_proxy.py — Hyprland IPC command-socket proxy for Lua config mode.

Waybar (and other tools) send old-style dispatch strings like:
    dispatch workspace 2
    dispatch togglespecialworkspace magic

In Lua config mode, Hyprland evaluates the body as Lua, so those fail.
This proxy translates dispatches into valid Lua expressions before forwarding.

The event socket (.socket2.sock) is handled separately by socat in
waybar_autostart.sh — pure pass-through needs no Python overhead.

Usage:
    Launched by waybar_autostart.sh which sets HYPRLAND_INSTANCE_SIGNATURE
    to point waybar at the proxy directory instead of the real socket.
"""

import asyncio
import os
import re
import sys
from contextlib import suppress

# Real Hyprland socket directory
REAL_SIG  = os.environ["HYPRLAND_INSTANCE_SIGNATURE"]
RUNTIME   = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
REAL_DIR  = f"{RUNTIME}/hypr/{REAL_SIG}"

# Proxy socket directory (predictable name, not session-specific)
PROXY_DIR = f"{RUNTIME}/hypr/lua_proxy"


# ---------------------------------------------------------------------------
# Dispatch translation table
# ---------------------------------------------------------------------------

def translate(raw: str) -> str:
    """Translate one old-style dispatch string to Lua, or return unchanged."""
    s = raw.strip()

    # dispatch workspace <target>
    m = re.match(r"^dispatch\s+workspace\s+(.+)$", s)
    if m:
        t = m.group(1).strip()
        if re.match(r"^-?\d+$", t):
            return f"dispatch hl.dsp.focus({{workspace={t}}})"
        return f'dispatch hl.dsp.focus({{workspace="{t}"}})'

    # dispatch movetoworkspace <target>
    m = re.match(r"^dispatch\s+movetoworkspace\s+(.+)$", s)
    if m:
        t = m.group(1).strip()
        if re.match(r"^-?\d+$", t):
            return f"dispatch hl.dsp.window.move({{workspace={t}}})"
        return f'dispatch hl.dsp.window.move({{workspace="{t}"}})'

    # dispatch movetoworkspacesilent <target>
    m = re.match(r"^dispatch\s+movetoworkspacesilent\s+(.+)$", s)
    if m:
        t = m.group(1).strip()
        if re.match(r"^-?\d+$", t):
            return f"dispatch hl.dsp.window.move({{workspace={t}, silent=true}})"
        return f'dispatch hl.dsp.window.move({{workspace="{t}", silent=true}})'

    # dispatch togglespecialworkspace [name]
    m = re.match(r"^dispatch\s+togglespecialworkspace\s*(.*)$", s)
    if m:
        name = m.group(1).strip()
        if name:
            return f'dispatch hl.dsp.workspace.toggle_special("{name}")'
        return "dispatch hl.dsp.workspace.toggle_special()"

    return raw  # pass through unchanged


# ---------------------------------------------------------------------------
# Command socket proxy (.socket.sock) — request/response, translates dispatches
# ---------------------------------------------------------------------------

async def handle_command(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    try:
        data = await asyncio.wait_for(reader.read(4096), timeout=2.0)

        cmd = data.decode("utf-8", errors="replace")
        out = translate(cmd)

        real_reader, real_writer = await asyncio.open_unix_connection(
            f"{REAL_DIR}/.socket.sock"
        )
        try:
            real_writer.write(out.encode("utf-8"))
            real_writer.write_eof()
            await real_writer.drain()

            try:
                resp = await asyncio.wait_for(real_reader.read(65536), timeout=0.5)
            except asyncio.TimeoutError:
                resp = b""
        finally:
            real_writer.close()
            with suppress(Exception):
                await real_writer.wait_closed()

        writer.write(resp)
        await writer.drain()
    except Exception as e:
        print(f"[proxy] command error: {e}", file=sys.stderr)
    finally:
        with suppress(Exception):
            writer.close()
        with suppress(Exception):
            await writer.wait_closed()


# ---------------------------------------------------------------------------
# Entry point — command socket only; event socket handled by socat
# ---------------------------------------------------------------------------

async def main() -> None:
    os.makedirs(PROXY_DIR, exist_ok=True)

    sock_path = f"{PROXY_DIR}/.socket.sock"
    with suppress(FileNotFoundError):
        os.unlink(sock_path)

    server = await asyncio.start_unix_server(handle_command, sock_path)
    print(f"[proxy] command socket: {sock_path}", file=sys.stderr)

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
