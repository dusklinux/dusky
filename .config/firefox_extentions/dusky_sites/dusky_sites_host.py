#!/usr/bin/env python3
"""
🦊 Dusky Sites Native Messaging Host (Arch Linux / Python 3.12+ / Firefox 115+)
=============================================================================
Event-driven Native Messaging Host with inotify watcher & wake-pipe IPC.
Guarantees low-latency FETCH_NOW and single-source-of-truth config persistence.
"""

from __future__ import annotations

import sys
import json
import struct
import os
import time
import re
import hashlib
import threading
import traceback
import ctypes
import ctypes.util
import select
from pathlib import Path

# Firefox native messaging: max message size from native app is 1 MiB
MAX_NATIVE_MSG = 1 * 1024 * 1024

# Linux inotify flags
IN_CLOEXEC = 0x80000
IN_NONBLOCK = 0x800
IN_ATTRIB = 0x00000004
IN_MODIFY = 0x00000002
IN_CLOSE_WRITE = 0x00000008
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200

CONFIG_PATH = Path.home() / ".config" / "dusky" / "settings" / "dusky_sites" / "config.json"

# Cross-thread wakeup pipe for main select() loop (Linux atomic pipe2)
_wake_r, _wake_w = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)

def wake_main() -> None:
    """Wakes the main select() loop instantly across threads."""
    try:
        os.write(_wake_w, b"\0")
    except OSError:
        pass

def drain_fd(fd: int, chunk: int = 65536) -> None:
    """Drains pending bytes from a non-blocking file descriptor."""
    while True:
        try:
            data = os.read(fd, chunk)
            if not data:
                break
        except (BlockingIOError, OSError):
            break

# --- Linux Inotify Event Watcher ---
class InotifyWatcher:
    """Non-blocking Linux inotify watcher integrated with wake-pipe select()."""

    def __init__(self) -> None:
        self.fd: int = -1
        self.watches: dict[str, int] = {}
        self.libc: ctypes.CDLL | None = None
        try:
            libname = ctypes.util.find_library("c") or "libc.so.6"
            self.libc = ctypes.CDLL(libname, use_errno=True)
            self.libc.inotify_init1.argtypes = [ctypes.c_int]
            self.libc.inotify_init1.restype = ctypes.c_int
            self.libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
            self.libc.inotify_add_watch.restype = ctypes.c_int
            self.libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]
            self.libc.inotify_rm_watch.restype = ctypes.c_int
            self.libc.close.argtypes = [ctypes.c_int]
            self.libc.close.restype = ctypes.c_int
            fd = self.libc.inotify_init1(IN_CLOEXEC | IN_NONBLOCK)
            self.fd = fd if fd >= 0 else -1
        except Exception:
            self.fd = -1
            self.libc = None

    def is_available(self) -> bool:
        return self.fd >= 0 and self.libc is not None

    def _watch_dir_for(self, path_str: str) -> Path | None:
        if not path_str:
            return None
        path = Path(path_str).expanduser()
        watch_dir = path if path.is_dir() else path.parent
        if not watch_dir.exists():
            try:
                watch_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                return None
        try:
            return watch_dir.resolve()
        except OSError:
            return watch_dir

    def sync_watches(self, path_strs: list[str]) -> None:
        """Syncs active inotify watch descriptors to match target directories."""
        desired: set[str] = set()
        for p in path_strs:
            d = self._watch_dir_for(p)
            if d is not None:
                desired.add(str(d))

        if self.is_available():
            for old, wd in list(self.watches.items()):
                if old not in desired:
                    try:
                        self.libc.inotify_rm_watch(self.fd, wd)
                    except Exception:
                        pass
                    self.watches.pop(old, None)

            mask = IN_MODIFY | IN_CLOSE_WRITE | IN_MOVED_TO | IN_CREATE | IN_DELETE | IN_ATTRIB
            for watch_str in desired:
                if watch_str not in self.watches:
                    try:
                        wd = self.libc.inotify_add_watch(self.fd, watch_str.encode("utf-8"), mask)
                        if wd >= 0:
                            self.watches[watch_str] = wd
                    except Exception:
                        pass

    def wait(self, timeout: float = 60.0) -> None:
        """Blocks until inotify event, wake_main() call, or timeout."""
        fds = [_wake_r]
        if self.is_available() and self.watches:
            fds.append(self.fd)
        try:
            readable, _, _ = select.select(fds, [], [], timeout)
        except Exception:
            time.sleep(min(timeout, 2.0))
            return

        if _wake_r in readable:
            drain_fd(_wake_r)
        if self.fd in readable and self.fd >= 0:
            drain_fd(self.fd)

    def close(self) -> None:
        if self.is_available():
            for wd in list(self.watches.values()):
                try:
                    self.libc.inotify_rm_watch(self.fd, wd)
                except Exception:
                    pass
            self.watches.clear()
            try:
                self.libc.close(self.fd)
            except Exception:
                pass
        self.fd = -1

# --- Configuration & Persistence ---
def _as_bool(raw: object) -> bool:
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return bool(raw)

def _norm_sites(sites: object) -> list[str]:
    if not isinstance(sites, list):
        return []
    return sorted({str(s).strip().lower() for s in sites if s})

def default_config() -> dict:
    return {
        "colors_file": str(Path.home() / ".config/matugen/generated/dusky_sites.css"),
        "websites_dir": str(Path.home() / ".config/dusky_sites"),
        "web_theme_enabled": False,
        "browser_theme_enabled": True,
        "eco_mode": True,
        "disabled_sites": [],
    }

def load_config_file() -> dict:
    cfg = default_config()
    if not CONFIG_PATH.is_file():
        return cfg
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return cfg
        if data.get("colorsPath"):
            cfg["colors_file"] = str(Path(data["colorsPath"]).expanduser())
        if data.get("websitesDir"):
            cfg["websites_dir"] = str(Path(data["websitesDir"]).expanduser())
        if "webThemeEnabled" in data:
            cfg["web_theme_enabled"] = _as_bool(data["webThemeEnabled"])
        if "browserThemeEnabled" in data:
            cfg["browser_theme_enabled"] = _as_bool(data["browserThemeEnabled"])
        if "ecoMode" in data:
            cfg["eco_mode"] = _as_bool(data["ecoMode"])
        if "disabledSites" in data:
            cfg["disabled_sites"] = _norm_sites(data.get("disabledSites"))
    except Exception:
        pass
    return cfg

def persist_config(cfg: dict) -> None:
    """Write-through merges host configuration to disk cleanly outside locks."""
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing: dict = {}
        if CONFIG_PATH.is_file():
            try:
                loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    existing = loaded
            except Exception:
                existing = {}
        existing["colorsPath"] = cfg["colors_file"]
        existing["websitesDir"] = cfg["websites_dir"]
        existing["webThemeEnabled"] = bool(cfg["web_theme_enabled"])
        existing["disabledSites"] = list(cfg["disabled_sites"])

        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        tmp.replace(CONFIG_PATH)
    except Exception as e:
        print(f"Dusky Sites host error (persist_config): {e}", file=sys.stderr)

config_lock = threading.Lock()
config = load_config_file()
fetch_lock = threading.Lock()
fetch_requested: bool = False
stdout_lock = threading.Lock()
running: bool = True

# --- Filesystem State ---
def get_dir_state(dirpath: str) -> dict[str, float]:
    p = Path(dirpath).expanduser() if dirpath else None
    if not p or not p.is_dir():
        return {}
    state: dict[str, float] = {}
    try:
        for f in p.glob("*.css"):
            try:
                state[f.name] = f.stat().st_mtime
            except OSError:
                continue
    except OSError:
        pass
    return state

# --- Native Messaging Binary Protocol ---
def get_message() -> dict | str | None:
    raw_length = sys.stdin.buffer.read(4)
    if len(raw_length) == 0:
        return "EOF"
    if len(raw_length) < 4:
        return None
    message_length = struct.unpack("=I", raw_length)[0]
    if message_length == 0 or message_length > MAX_NATIVE_MSG:
        return None
    msg_bytes = sys.stdin.buffer.read(message_length)
    if len(msg_bytes) < message_length:
        return "EOF"
    try:
        return json.loads(msg_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "DECODE_ERROR"

def send_message(message_content: dict) -> bool:
    """Send one NMH message. Returns True only if fully written to stdout."""
    try:
        def _encode(obj: dict) -> bytes:
            return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        encoded_content = _encode(message_content)

        if len(encoded_content) > MAX_NATIVE_MSG:
            if message_content.get("type") == "MATUGEN_UPDATE" and isinstance(message_content.get("data"), dict):
                slim = dict(message_content)
                slim_data = dict(message_content["data"])
                slim_data["websites"] = {}
                status = list(slim_data.get("status") or [])
                status.append("websites omitted: exceeded 1MiB native messaging limit")
                slim_data["status"] = status
                slim_data["ok"] = bool(slim_data.get("colors"))
                slim["data"] = slim_data
                encoded_content = _encode(slim)

        if len(encoded_content) > MAX_NATIVE_MSG:
            print("Dusky Sites host error: outbound message exceeds 1MiB native limit", file=sys.stderr)
            return False

        encoded_length = struct.pack("=I", len(encoded_content))
        with stdout_lock:
            sys.stdout.buffer.write(encoded_length)
            sys.stdout.buffer.write(encoded_content)
            sys.stdout.buffer.flush()
        return True
    except Exception as e:
        print(f"Dusky Sites host error (send_message): {e}", file=sys.stderr)
        return False

# --- CSS Parsers ---
_COLOR_RE = re.compile(r"(--[\w-]+)\s*:\s*([^;]+?)\s*(?:!important)?\s*;", re.IGNORECASE)
_MOZ_DOMAIN_RE = re.compile(r"@-moz-document\s+(?P<specs>[^{]+)\{", re.IGNORECASE)
_DOMAIN_SPEC_RE = re.compile(r'domain\(\s*["\']([^"\']+)["\']\s*\)', re.IGNORECASE)

def _extract_balanced_block(content: str, open_brace_idx: int) -> str:
    brace_count = 0
    in_string = False
    string_char = ""
    in_block_comment = False
    in_line_comment = False
    i = open_brace_idx
    n = len(content)
    while i < n:
        ch = content[i]
        nxt = content[i + 1] if i + 1 < n else ""
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
                continue
        elif in_line_comment:
            if ch == "\n":
                in_line_comment = False
        elif in_string:
            if ch == "\\":
                i += 2
                continue
            if ch == string_char:
                in_string = False
        else:
            if ch == "/" and nxt == "*":
                in_block_comment = True
                i += 2
                continue
            if ch == "/" and nxt == "/":
                in_line_comment = True
                i += 2
                continue
            if ch in ("'", '"'):
                in_string = True
                string_char = ch
            elif ch == "{":
                brace_count += 1
            elif ch == "}":
                brace_count -= 1
                if brace_count == 0:
                    return content[open_brace_idx + 1 : i].strip()
        i += 1
    return content[open_brace_idx + 1 :].strip()

def parse_colors(colors_file: str) -> dict[str, str]:
    p = Path(colors_file).expanduser() if colors_file else None
    if not p or not p.is_file():
        return {}
    try:
        content = p.read_text(encoding="utf-8")
        return {name.strip(): value.strip() for name, value in _COLOR_RE.findall(content)}
    except Exception:
        return {}

def parse_websites(websites_dir: str, disabled_sites: list[str] | None = None) -> dict[str, str]:
    p = Path(websites_dir).expanduser() if websites_dir else None
    if not p or not p.is_dir():
        return {}
    disabled_set = {s.lower() for s in (disabled_sites or []) if s}
    websites: dict[str, str] = {}
    try:
        for filepath in p.glob("*.css"):
            try:
                stem = filepath.stem.lower()
                if stem in disabled_set:
                    continue
                content = filepath.read_text(encoding="utf-8")
                matches = list(_MOZ_DOMAIN_RE.finditer(content))
                if not matches:
                    websites[stem] = content.strip()
                    continue
                for m in matches:
                    domains = [d.lower() for d in _DOMAIN_SPEC_RE.findall(m.group("specs"))]
                    body = _extract_balanced_block(content, m.end() - 1)
                    for domain in domains:
                        if domain in disabled_set:
                            continue
                        websites[domain] = body
            except Exception:
                continue
    except Exception:
        pass
    return websites

def get_theme_data(colors_file: str, websites_dir: str, web_theme_enabled: bool | None = None, disabled_sites: list[str] | None = None, browser_theme_enabled: bool = True, eco_mode: bool = True) -> dict:
    status: list[str] = []
    p_colors = Path(colors_file).expanduser() if colors_file else None
    p_sites = Path(websites_dir).expanduser() if websites_dir else None

    if not p_colors or not p_colors.is_file():
        status.append(f"Colors file not found: {colors_file}")
    if p_sites and not p_sites.is_dir():
        status.append(f"Websites dir not found: {websites_dir}")

    disabled = _norm_sites(disabled_sites)
    colors = parse_colors(colors_file)
    websites = parse_websites(websites_dir, disabled)

    if not colors and not any("not found" in s.lower() for s in status):
        status.append(f"Colors empty or unreadable: {colors_file}")

    return {
        "colors": colors,
        "websites": websites,
        "disabledSites": disabled,
        "webThemeEnabled": bool(web_theme_enabled),
        "browserThemeEnabled": bool(browser_theme_enabled),
        "ecoMode": bool(eco_mode),
        "status": status if status else ["OK"],
        "ok": bool(colors),
    }

def get_data_hash(data: dict) -> str:
    payload = {k: v for k, v in data.items() if k != "timestamp"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()

def apply_set_config(new_config: dict) -> bool:
    changed = False
    snapshot: dict | None = None
    with config_lock:
        if new_config.get("colorsPath"):
            v = str(Path(new_config["colorsPath"]).expanduser())
            if config["colors_file"] != v:
                config["colors_file"] = v
                changed = True
        if new_config.get("websitesDir"):
            v = str(Path(new_config["websitesDir"]).expanduser())
            if config["websites_dir"] != v:
                config["websites_dir"] = v
                changed = True
        if "webThemeEnabled" in new_config:
            v = _as_bool(new_config["webThemeEnabled"])
            if config["web_theme_enabled"] != v:
                config["web_theme_enabled"] = v
                changed = True
        if isinstance(new_config.get("disabledSites"), list):
            v = _norm_sites(new_config["disabledSites"])
            if config["disabled_sites"] != v:
                config["disabled_sites"] = v
                changed = True
        if changed:
            snapshot = {
                "colors_file": config["colors_file"],
                "websites_dir": config["websites_dir"],
                "web_theme_enabled": config["web_theme_enabled"],
                "disabled_sites": list(config["disabled_sites"]),
            }
    if snapshot is not None:
        persist_config(snapshot)
    return changed

def message_handler() -> None:
    global running, fetch_requested
    error_count = 0
    while running:
        try:
            msg = get_message()
            if msg == "EOF":
                running = False
                wake_main()
                break
            if msg in ("DECODE_ERROR", None):
                error_count += 1
                if error_count > 10:
                    running = False
                    wake_main()
                    break
                time.sleep(0.5)
                continue

            error_count = 0
            if not isinstance(msg, dict):
                continue

            msg_type = msg.get("type")

            if msg_type == "SET_CONFIG":
                apply_set_config(msg.get("config", {}) or {})
                with fetch_lock:
                    fetch_requested = True
                wake_main()

            elif msg_type == "FETCH_NOW":
                with fetch_lock:
                    fetch_requested = True
                wake_main()

            elif msg_type == "LIVE_THEME_RESPONSE":
                theme_data = msg.get("theme", {})
                cache_file = Path.home() / ".config/dusky/settings/dusky_sites/live_theme_cache.json"
                try:
                    cache_file.parent.mkdir(parents=True, exist_ok=True)
                    tmp = cache_file.with_suffix(".json.tmp")
                    tmp.write_text(json.dumps(theme_data, indent=2) + "\n", encoding="utf-8")
                    tmp.replace(cache_file)
                except Exception:
                    pass

            elif msg_type in {"GET_PROFILE_PATHS", "WRITE_USER_CHROME", "WRITE_USER_CONTENT", "SET_FONT_SIZE", "QUERY_LIVE_THEME"}:
                send_message({
                    "type": "HOST_RESPONSE",
                    "ok": False,
                    "error": f"unsupported_message:{msg_type}",
                    "echo": msg_type,
                })

        except Exception as e:
            print(f"Dusky Sites host error (handler): {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

def main() -> None:
    global running, fetch_requested
    threading.Thread(target=message_handler, daemon=True).start()

    watcher = InotifyWatcher()
    last_hash = ""
    last_colors_mtime = -1.0
    last_websites_state: dict[str, float] | None = None
    last_config_mtime = -1.0
    force_send = True

    while running:
        try:
            current_config_mtime = -1.0
            if CONFIG_PATH.is_file():
                try:
                    current_config_mtime = CONFIG_PATH.stat().st_mtime
                except OSError:
                    pass

            if current_config_mtime != last_config_mtime:
                last_config_mtime = current_config_mtime
                disk_cfg = load_config_file()
                with config_lock:
                    config.update(disk_cfg)
                force_send = True

            with config_lock:
                colors_file = config["colors_file"]
                websites_dir = config["websites_dir"]
                web_enabled = bool(config["web_theme_enabled"])
                browser_theme_enabled = bool(config.get("browser_theme_enabled", True))
                eco_mode = bool(config.get("eco_mode", True))
                disabled_sites = list(config["disabled_sites"])

            watcher.sync_watches([str(CONFIG_PATH), colors_file, websites_dir])

            should_update = force_send

            with fetch_lock:
                if fetch_requested:
                    should_update = True
                    force_send = True
                    fetch_requested = False

            current_colors_mtime = -1.0
            p_colors = Path(colors_file).expanduser() if colors_file else None
            if p_colors and p_colors.is_file():
                try:
                    current_colors_mtime = p_colors.stat().st_mtime
                except OSError:
                    pass
            if current_colors_mtime != last_colors_mtime:
                last_colors_mtime = current_colors_mtime
                should_update = True

            current_websites_state = get_dir_state(websites_dir)
            if current_websites_state != last_websites_state:
                last_websites_state = current_websites_state
                should_update = True

            send_failed = False
            if should_update or not last_hash:
                data = get_theme_data(colors_file, websites_dir, web_enabled, disabled_sites, browser_theme_enabled, eco_mode)
                current_hash = get_data_hash(data)
                if current_hash != last_hash or force_send:
                    data["timestamp"] = time.time()
                    if send_message({"type": "MATUGEN_UPDATE", "data": data}):
                        last_hash = current_hash
                        force_send = False
                    else:
                        force_send = True
                        send_failed = True
                else:
                    force_send = False

            if not running:
                break

            if send_failed:
                watcher.wait(timeout=1.0)
            else:
                watcher.wait(timeout=60.0)
                if not running:
                    break
                deadline = time.monotonic() + 0.05
                while running and time.monotonic() < deadline:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    watcher.wait(timeout=remaining)

        except Exception as e:
            print(f"Dusky Sites host error (main): {e}", file=sys.stderr)
            time.sleep(5.0)

    watcher.close()
    try:
        os.close(_wake_r)
        os.close(_wake_w)
    except OSError:
        pass
    sys.exit(0)

if __name__ == "__main__":
    main()