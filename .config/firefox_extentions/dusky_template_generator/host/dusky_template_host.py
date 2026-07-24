#!/usr/bin/env python3
"""
🦊 Dusky Template Generator Native Messaging Host
=================================================
Receives generated template CSS from the WebExtension and writes
it directly to ~/.config/dusky_sites/<domain>.css.
"""

from __future__ import annotations

import sys
import json
import struct
import re
import os
from pathlib import Path

MAX_NATIVE_MSG = 1 * 1024 * 1024  # 1 MiB

def get_message() -> dict | None:
    raw_length = sys.stdin.buffer.read(4)
    if len(raw_length) < 4:
        return None
    message_length = struct.unpack("=I", raw_length)[0]
    if message_length == 0 or message_length > MAX_NATIVE_MSG:
        return None
    msg_bytes = sys.stdin.buffer.read(message_length)
    if len(msg_bytes) < message_length:
        return None
    try:
        return json.loads(msg_bytes.decode("utf-8"))
    except Exception:
        return None

def send_message(msg: dict) -> None:
    try:
        encoded = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        sys.stdout.buffer.write(struct.pack("=I", len(encoded)))
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
    except Exception as e:
        print(f"Host send error: {e}", file=sys.stderr)

def sanitize_domain(domain: str) -> str:
    domain = domain.strip().lower()
    domain = re.sub(r"https?://", "", domain)
    domain = domain.split("/")[0].split(":")[0]
    domain = re.sub(r"[^\w.-]", "", domain)
    return domain.removeprefix("www.")

def main() -> None:
    target_dir = Path.home() / ".config" / "dusky_sites"
    target_dir.mkdir(parents=True, exist_ok=True)

    while True:
        msg = get_message()
        if msg is None:
            break

        msg_type = msg.get("type")
        if msg_type == "SAVE_TEMPLATE":
            domain = sanitize_domain(str(msg.get("domain", "")))
            css_content = str(msg.get("css", ""))

            if not domain:
                send_message({"status": "error", "error": "Invalid domain name"})
                continue
            if not css_content.strip():
                send_message({"status": "error", "error": "CSS content is empty"})
                continue

            file_path = target_dir / f"{domain}.css"
            tmp_path = file_path.with_name(f"{file_path.name}.tmp")
            try:
                tmp_path.write_text(css_content, encoding="utf-8")
                tmp_path.replace(file_path)
                send_message({
                    "status": "ok",
                    "domain": domain,
                    "file": str(file_path),
                    "message": f"Saved {file_path.name} to ~/.config/dusky_sites/"
                })
            except Exception as e:
                send_message({"status": "error", "error": f"Write failed: {e}"})

        elif msg_type == "PING":
            send_message({"status": "ok", "pong": True})
        else:
            send_message({"status": "error", "error": f"Unknown message type: {msg_type}"})

if __name__ == "__main__":
    main()
