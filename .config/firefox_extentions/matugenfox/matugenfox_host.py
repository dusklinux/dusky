#!/usr/bin/env python3
"""
🦊 MatugenFox Native Messaging Host (Modern Arch Linux / Python 3.12+)
======================================================================
Event-driven, zero-wakeup Native Messaging Host for Firefox.
Watches Matugen generated color palettes using Linux C-library inotify.
"""

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
import select
from pathlib import Path

# --- Inotify Event-Driven File Watcher ---
class InotifyWatcher:
    """Zero-wakeup event-driven Linux inotify file watcher using standard C libraries (libc)."""
    def __init__(self):
        self.fd = -1
        self.watches = {}
        try:
            self.libc = ctypes.CDLL(None)
            self.libc.inotify_init1.argtypes = [ctypes.c_int]
            self.libc.inotify_init1.restype = ctypes.c_int
            self.libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
            self.libc.inotify_add_watch.restype = ctypes.c_int
            self.libc.close.argtypes = [ctypes.c_int]
            self.libc.close.restype = ctypes.c_int
            # IN_CLOEXEC (0x80000) | IN_NONBLOCK (0x800)
            self.fd = self.libc.inotify_init1(0x80000 | 0x800)
        except Exception:
            self.fd = -1

    def is_available(self) -> bool:
        return self.fd >= 0

    def add_watch(self, path_str: str):
        if not self.is_available() or not path_str:
            return
        path = Path(path_str).expanduser()
        watch_dir = path if path.is_dir() else path.parent
        watch_str = str(watch_dir)

        if watch_str in self.watches:
            return

        if not watch_dir.exists():
            try:
                watch_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                return

        # IN_MODIFY (0x2) | IN_CLOSE_WRITE (0x8) | IN_MOVED_TO (0x80) | IN_CREATE (0x100) | IN_DELETE (0x200)
        mask = 0x02 | 0x08 | 0x80 | 0x100 | 0x200
        try:
            wd = self.libc.inotify_add_watch(self.fd, watch_str.encode('utf-8'), mask)
            if wd >= 0:
                self.watches[watch_str] = wd
        except Exception:
            pass

    def wait_for_events(self, timeout: float = 60.0):
        """Block until a file event occurs or timeout expires."""
        if not self.is_available() or not self.watches:
            poll_event.wait(2.0)
            poll_event.clear()
            return
        try:
            r, _, _ = select.select([self.fd], [], [], timeout)
            if r:
                try:
                    os.read(self.fd, 4096)
                except Exception:
                    pass
        except Exception:
            poll_event.wait(2.0)
            poll_event.clear()

    def close(self):
        if self.fd >= 0:
            try:
                self.libc.close(self.fd)
            except Exception:
                pass
            self.fd = -1

# --- Global State ---
def load_external_config() -> tuple[str, str, bool | None, list[str]]:
    config_file = Path.home() / ".config/dusky/settings/matugenfox/config.json"
    colors_file = str(Path.home() / ".config/matugen/generated/firefox_websites.css")
    websites_dir = str(Path.home() / ".config/dusky_sites")
    web_theme_enabled = None
    disabled_sites = []

    if config_file.is_file():
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if data.get('colorsPath'):
                    colors_file = str(Path(data['colorsPath']).expanduser())
                if data.get('websitesDir'):
                    websites_dir = str(Path(data['websitesDir']).expanduser())
                if 'webThemeEnabled' in data:
                    web_theme_enabled = bool(data['webThemeEnabled'])
                if isinstance(data.get('disabledSites'), list):
                    disabled_sites = [str(s).strip().lower() for s in data['disabledSites'] if s]
        except Exception:
            pass
    return colors_file, websites_dir, web_theme_enabled, disabled_sites

_default_colors, _default_websites, _default_web_enabled, _default_disabled_sites = load_external_config()
config = {
    "colors_file": _default_colors,
    "websites_dir": _default_websites
}
config_lock = threading.Lock()
stdout_lock = threading.Lock()
running = True
force_update = False
poll_event = threading.Event()

# --- Directory State ---
def get_dir_state(dirpath: str) -> dict[str, float]:
    p = Path(dirpath).expanduser() if dirpath else None
    if not p or not p.is_dir():
        return {}
    state = {}
    try:
        for f in p.glob("*.css"):
            try:
                state[f.name] = f.stat().st_mtime
            except OSError:
                continue
    except OSError:
        pass
    return state

# --- Native Messaging Protocol ---
def get_message() -> dict | str | None:
    raw_length = sys.stdin.buffer.read(4)
    if len(raw_length) == 0:
        return "EOF"
    if len(raw_length) < 4:
        return None
    message_length = struct.unpack('=I', raw_length)[0]
    if message_length > 10 * 1024 * 1024:  # 10MB safety cap
        return None
    msg_bytes = sys.stdin.buffer.read(message_length)
    if len(msg_bytes) < message_length:
        return "EOF"
    try:
        return json.loads(msg_bytes.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "DECODE_ERROR"

def send_message(message_content: dict):
    try:
        encoded_content = json.dumps(message_content).encode('utf-8')
        encoded_length = struct.pack('=I', len(encoded_content))
        with stdout_lock:
            sys.stdout.buffer.write(encoded_length)
            sys.stdout.buffer.write(encoded_content)
            sys.stdout.buffer.flush()
    except Exception as e:
        print(f"MatugenFox host error (send_message): {e}", file=sys.stderr)

# --- Color & Template Parsing ---
def parse_colors(colors_file: str) -> dict[str, str]:
    p = Path(colors_file).expanduser() if colors_file else None
    if not p or not p.is_file():
        return {}
    try:
        content = p.read_text(encoding='utf-8')
        matches = re.findall(r'(--[\w-]+):\s*([^;]+);', content)
        return {name.strip(): value.strip() for name, value in matches}
    except Exception:
        return {}

def parse_websites(websites_dir: str, disabled_sites: list[str] = None) -> dict[str, str]:
    p = Path(websites_dir).expanduser() if websites_dir else None
    if not p or not p.is_dir():
        return {}
    disabled_set = set(disabled_sites) if disabled_sites else set()
    websites = {}
    try:
        for filepath in p.glob("*.css"):
            try:
                content = filepath.read_text(encoding='utf-8')
                match = re.search(r'@-moz-document\s+domain\("([^"]+)"\)\s*\{', content)
                if match:
                    domain = match.group(1).lower()
                    if domain in disabled_set:
                        continue
                    start_idx = match.end() - 1
                    brace_count = 0
                    in_string = False
                    string_char = ''
                    in_comment = False
                    end_idx = -1
                    i = start_idx
                    while i < len(content):
                        char = content[i]
                        if in_comment:
                            if char == '*' and i + 1 < len(content) and content[i+1] == '/':
                                in_comment = False
                                i += 1
                        elif in_string:
                            if char == '\\':
                                i += 1
                            elif char == string_char:
                                in_string = False
                        else:
                            if char == '/' and i + 1 < len(content) and content[i+1] == '*':
                                in_comment = True
                                i += 1
                            elif char in ("'", '"'):
                                in_string = True
                                string_char = char
                            elif char == '{':
                                brace_count += 1
                            elif char == '}':
                                brace_count -= 1
                                if brace_count == 0:
                                    end_idx = i
                                    break
                        i += 1
                    if end_idx != -1:
                        websites[domain] = content[start_idx+1:end_idx].strip()
                    else:
                        websites[domain] = content[start_idx+1:].strip()
                else:
                    domain = filepath.stem.lower()
                    if domain in disabled_set:
                        continue
                    websites[domain] = content.strip()
            except Exception:
                continue
    except Exception:
        pass
    return websites

def get_theme_data(colors_file: str, websites_dir: str, web_theme_enabled: bool | None = None, disabled_sites: list[str] = None) -> dict:
    status = []
    p_colors = Path(colors_file).expanduser() if colors_file else None
    p_sites = Path(websites_dir).expanduser() if websites_dir else None

    if not p_colors or not p_colors.is_file():
        status.append(f"Colors file not found: {colors_file}")
    if p_sites and not p_sites.is_dir():
        status.append(f"Websites dir not found: {websites_dir}")

    data = {
        "colors": parse_colors(colors_file),
        "websites": parse_websites(websites_dir, disabled_sites),
        "disabledSites": disabled_sites if disabled_sites else [],
        "status": status if status else ["OK"]
    }
    if web_theme_enabled is not None:
        data["webThemeEnabled"] = web_theme_enabled
    return data

def get_data_hash(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode('utf-8')).hexdigest()

_stored_config_cache = {}

def message_handler():
    global config, running, _stored_config_cache
    error_count = 0
    while running:
        try:
            msg = get_message()
            if msg == "EOF":
                running = False
                break
            elif msg in ("DECODE_ERROR", None):
                error_count += 1
                if error_count > 10:
                    running = False
                    break
                time.sleep(0.5)
                continue

            error_count = 0
            msg_type = msg.get("type")

            if msg_type == "SET_CONFIG":
                new_config = msg.get("config", {})
                with config_lock:
                    if new_config.get("colorsPath"):
                        config["colors_file"] = str(Path(new_config["colorsPath"]).expanduser())
                    if new_config.get("websitesDir"):
                        config["websites_dir"] = str(Path(new_config["websitesDir"]).expanduser())
                    _stored_config_cache = new_config

            elif msg_type == "FETCH_NOW":
                global force_update
                force_update = True
                poll_event.set()

        except Exception as e:
            print(f"MatugenFox host error (handler): {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

def main():
    global running, force_update
    threading.Thread(target=message_handler, daemon=True).start()

    watcher = InotifyWatcher()
    last_hash = ""
    last_colors_mtime = -1.0
    last_websites_state = None
    last_config_mtime = -1.0

    config_file_path = Path.home() / ".config/dusky/settings/matugenfox/config.json"
    if config_file_path.parent.exists():
        watcher.add_watch(str(config_file_path.parent))

    while running:
        try:
            ext_colors, ext_websites, ext_web_enabled, ext_disabled_sites = load_external_config()
            with config_lock:
                colors_file = config["colors_file"] or ext_colors
                websites_dir = config["websites_dir"] or ext_websites

            if colors_file:
                watcher.add_watch(colors_file)
            if websites_dir:
                watcher.add_watch(websites_dir)

            should_update = False

            # Check config file mtime
            current_config_mtime = -1.0
            if config_file_path.is_file():
                try:
                    current_config_mtime = config_file_path.stat().st_mtime
                except OSError:
                    pass

            if current_config_mtime != last_config_mtime:
                last_config_mtime = current_config_mtime
                should_update = True

            # Check colors file mtime
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

            # Check websites directory state
            current_websites_state = get_dir_state(websites_dir)
            if current_websites_state != last_websites_state:
                last_websites_state = current_websites_state
                should_update = True

            if should_update or not last_hash or force_update:
                data = get_theme_data(colors_file, websites_dir, ext_web_enabled, ext_disabled_sites)
                current_hash = get_data_hash(data)

                if current_hash != last_hash or force_update:
                    last_hash = current_hash
                    force_update = False
                    data["timestamp"] = time.time()
                    send_message({"type": "MATUGEN_UPDATE", "data": data})

            if poll_event.is_set():
                poll_event.clear()
            else:
                watcher.wait_for_events(timeout=60.0)
        except Exception as e:
            print(f"MatugenFox host error (main): {e}", file=sys.stderr)
            time.sleep(5.0)

    watcher.close()
    sys.exit(0)

if __name__ == "__main__":
    main()