#!/usr/bin/env python3
import sys
import json
import struct
import os
import time
import re
import glob
import hashlib
import threading
import traceback
import configparser

# --- Global State ---
config = {
    "colors_file": None,
    "websites_dir": None
}
config_lock = threading.Lock()
stdout_lock = threading.Lock()  # Protection against concurrent protocol corruption
running = True

# --- XDG Config Path ---
def get_config_path():
    """Get a safe, user-writable path for the config file respecting XDG standard."""
    config_dir = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    app_dir = os.path.join(config_dir, 'matugenfox')
    os.makedirs(app_dir, exist_ok=True)
    return os.path.join(app_dir, 'config.json')

# --- Atomic Write ---
def atomic_write(filepath, content):
    """Atomically write string content to prevent TOC/TOU race conditions."""
    temp_path = f"{filepath}.tmp.{os.getpid()}"
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(temp_path, 'w', encoding='utf-8') as f:
        f.write(content)
    os.replace(temp_path, filepath)

def atomic_write_json(filepath, data):
    """Atomically write JSON content."""
    temp_path = f"{filepath}.tmp.{os.getpid()}"
    with open(temp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    os.replace(temp_path, filepath)

# --- Firefox Profile Detection ---
def find_firefox_profiles():
    """
    Auto-detect Firefox profile directories across all supported browsers.
    Returns a list of dicts: { browser, profile_name, profile_path, chrome_path }
    """
    profiles = []
    home = os.path.expanduser('~')

    # Browser profile directories to search
    browser_configs = [
        ("Firefox",   os.path.join(home, '.mozilla', 'firefox')),
        ("LibreWolf", os.path.join(home, '.librewolf')),
        ("Zen",       os.path.join(home, '.zen')),
        ("Waterfox",  os.path.join(home, '.waterfox')),
        ("Floorp",    os.path.join(home, '.floorp')),
        ("FireDragon",os.path.join(home, '.firedragon')),
        # Flatpak variants
        ("Firefox (Flatpak)",
            os.path.join(home, '.var', 'app', 'org.mozilla.firefox', '.mozilla', 'firefox')),
        ("LibreWolf (Flatpak)",
            os.path.join(home, '.var', 'app', 'io.gitlab.librewolf-community', '.librewolf')),
    ]

    for browser_name, profiles_dir in browser_configs:
        if not os.path.isdir(profiles_dir):
            continue
        # Try to read profiles.ini to find the default profile
        profiles_ini = os.path.join(profiles_dir, 'profiles.ini')
        if os.path.isfile(profiles_ini):
            try:
                cp = configparser.ConfigParser()
                cp.read(profiles_ini, encoding='utf-8')
                for section in cp.sections():
                    if not section.startswith('Profile'):
                        continue
                    if not cp.has_option(section, 'Path'):
                        continue
                    rel_path = cp.get(section, 'Path')
                    is_relative = cp.getboolean(section, 'IsRelative', fallback=True)
                    if is_relative:
                        profile_path = os.path.join(profiles_dir, rel_path)
                    else:
                        profile_path = rel_path
                    if os.path.isdir(profile_path):
                        chrome_dir = os.path.join(profile_path, 'chrome')
                        profiles.append({
                            "browser": browser_name,
                            "profile_name": rel_path.split('/')[-1] if '/' in rel_path else rel_path,
                            "profile_path": profile_path,
                            "chrome_path": chrome_dir,
                        })
            except Exception:
                pass
        else:
            # Fallback: just list directories that look like profile dirs
            try:
                for entry in os.listdir(profiles_dir):
                    profile_path = os.path.join(profiles_dir, entry)
                    if os.path.isdir(profile_path) and ('.' in entry or 'default' in entry.lower()):
                        chrome_dir = os.path.join(profile_path, 'chrome')
                        profiles.append({
                            "browser": browser_name,
                            "profile_name": entry,
                            "profile_path": profile_path,
                            "chrome_path": chrome_dir,
                        })
            except Exception:
                pass

    return profiles

def get_manual_profile_path():
    """Get the manually configured profile path from the stored config."""
    try:
        config_path = get_config_path()
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data.get('firefoxProfilePath'), data.get('chromeDir')
    except Exception:
        pass
    return None, None

def resolve_chrome_dir(stored_config=None):
    """
    Resolve the Firefox chrome/ directory.
    Priority: manual config path > auto-detected default profile.
    """
    # 1. Check manual config
    if stored_config:
        chrome_dir = stored_config.get('chromeDir') or stored_config.get('firefoxProfilePath')
        if chrome_dir:
            chrome_dir = os.path.expanduser(chrome_dir)
            if not chrome_dir.endswith('/chrome'):
                chrome_dir = os.path.join(chrome_dir, 'chrome')
            return chrome_dir

    # 2. Auto-detect: find default profile
    profiles = find_firefox_profiles()
    if profiles:
        # Prefer the first profile found (usually the default)
        return profiles[0]["chrome_path"]

    return None

# --- userChrome/userContent CSS ---
USER_CHROME_HEADER = "/* MatugenFox userChrome.css - Auto-generated, do not edit manually */\n"
USER_CONTENT_HEADER = "/* MatugenFox userContent.css - Auto-generated, do not edit manually */\n"

def write_user_css(target, enabled, chrome_dir, font_size=13):
    """Write or remove userChrome.css or userContent.css in the Firefox profile chrome/ dir."""
    if not chrome_dir:
        return False, "No Firefox profile directory found. Please set it manually in Options > System."

    filename = 'userChrome.css' if target == 'userChrome' else 'userContent.css'
    filepath = os.path.join(chrome_dir, filename)

    if not enabled:
        # Disable: remove the file (or comment out its content)
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception as e:
                return False, f"Failed to remove {filename}: {e}"
        return True, None

    # Enable: write the file
    os.makedirs(chrome_dir, exist_ok=True)

    if target == 'userChrome':
        content = f"{USER_CHROME_HEADER}/* Font size: {font_size}px */\n"
        content += f"""
/* ── Scrollbar ── */
:root {{
  --uc-base-font-size: {font_size}px;
  scrollbar-width: thin;
}}

/* ── Toolbar compact ── */
#nav-bar {{
  height: calc(var(--uc-base-font-size) * 2.8) !important;
}}

/* ── Context menu ── */
menupopup > menuitem,
menupopup > menu {{
  font-size: var(--uc-base-font-size) !important;
  min-height: calc(var(--uc-base-font-size) * 1.8) !important;
}}
"""
    else:
        content = f"{USER_CONTENT_HEADER}\n"
        content += """
/* ── Hide global scrollbar ── */
*:not(select):not(#scrollbar-container):not(.scrollbar-container) {{
  scrollbar-width: none !important;
}}
::-webkit-scrollbar {{
  display: none !important;
}}
"""

    try:
        atomic_write(filepath, content)
        return True, None
    except Exception as e:
        return False, f"Failed to write {filename}: {e}"

def update_font_size(chrome_dir, font_size):
    """Update the font size in userChrome.css."""
    if not chrome_dir:
        return False, "No chrome directory configured."
    filepath = os.path.join(chrome_dir, 'userChrome.css')
    if not os.path.exists(filepath):
        return False, "userChrome.css does not exist. Enable it first."
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        content = re.sub(
            r'--uc-base-font-size: \d+px',
            f'--uc-base-font-size: {font_size}px',
            content
        )
        content = re.sub(
            r'/\* Font size: \d+px \*/',
            f'/* Font size: {font_size}px */',
            content
        )
        atomic_write(filepath, content)
        return True, None
    except Exception as e:
        return False, str(e)

# --- Directory State ---
def get_dir_state(dirpath):
    """Generate a composite state dictionary of all relevant CSS files to track deletions."""
    if not dirpath or not os.path.isdir(dirpath):
        return {}
    state = {}
    try:
        for f in os.listdir(dirpath):
            if f.endswith(".css"):
                fpath = os.path.join(dirpath, f)
                try:
                    if os.path.isfile(fpath):
                        state[f] = os.path.getmtime(fpath)
                except OSError:
                    continue
    except OSError:
        pass
    return state

# --- Native Messaging Protocol ---
def get_message():
    """Read a message precisely adhering to the Native Messaging protocol."""
    raw_length = sys.stdin.buffer.read(4)
    if len(raw_length) == 0:
        return "EOF"
    if len(raw_length) < 4:
        return None
    message_length = struct.unpack('=I', raw_length)[0]
    if message_length > 10 * 1024 * 1024:  # 10MB sanity limit
        return None
    msg_bytes = b''
    while len(msg_bytes) < message_length:
        chunk = sys.stdin.buffer.read(message_length - len(msg_bytes))
        if not chunk:
            return "EOF"
        msg_bytes += chunk
    try:
        return json.loads(msg_bytes.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "DECODE_ERROR"

def send_message(message_content):
    """Safely encode and dispatch message over stdout with concurrency protection."""
    try:
        encoded_content = json.dumps(message_content).encode('utf-8')
        encoded_length = struct.pack('=I', len(encoded_content))
        with stdout_lock:
            sys.stdout.buffer.write(encoded_length)
            sys.stdout.buffer.write(encoded_content)
            sys.stdout.buffer.flush()
    except Exception as e:
        print(f"MatugenFox host error (send_message): {e}", file=sys.stderr)

# --- Color Parsing ---
def parse_colors(colors_file):
    if not colors_file or not os.path.exists(colors_file):
        return {}
    try:
        with open(colors_file, 'r', encoding='utf-8') as f:
            content = f.read()
        matches = re.findall(r'(--[\w-]+):\s*([^;]+);', content)
        return {name.strip(): value.strip() for name, value in matches}
    except Exception:
        return {}

def parse_websites(websites_dir):
    if not websites_dir or not os.path.isdir(websites_dir):
        return {}
    websites = {}
    try:
        for filename in os.listdir(websites_dir):
            if not filename.endswith(".css"):
                continue
            path = os.path.join(websites_dir, filename)
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                match = re.search(r'@-moz-document\s+domain\("([^"]+)"\)\s*\{', content)
                if match:
                    domain = match.group(1)
                    start_idx = match.end() - 1
                    brace_count = 0
                    in_string = False
                    string_char = ''
                    end_idx = -1
                    for i in range(start_idx, len(content)):
                        char = content[i]
                        if in_string:
                            if char == string_char and content[i-1] != '\\':
                                in_string = False
                        else:
                            if char in ("'", '"'):
                                in_string = True
                                string_char = char
                            elif char == '{':
                                brace_count += 1
                            elif char == '}':
                                brace_count -= 1
                                if brace_count == 0:
                                    end_idx = i
                                    break
                    if end_idx != -1:
                        websites[domain] = content[start_idx+1:end_idx].strip()
                    else:
                        websites[domain] = content[start_idx+1:].strip()
                else:
                    domain = filename.removesuffix(".css")
                    websites[domain] = content.strip()
            except Exception:
                continue
    except Exception:
        pass
    return websites

def get_theme_data(colors_file, websites_dir):
    status = []
    if not colors_file or not os.path.exists(colors_file):
        status.append(f"Colors file not found: {colors_file}")
    if websites_dir and not os.path.isdir(websites_dir):
        status.append(f"Websites dir not found: {websites_dir}")
    return {
        "colors": parse_colors(colors_file),
        "websites": parse_websites(websites_dir),
        "status": status if status else ["OK"]
    }

def get_data_hash(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode('utf-8')).hexdigest()

# ─── Stored config cache (for chrome dir resolution) ───
_stored_config_cache = {}

def message_handler():
    global config, running, _stored_config_cache
    while running:
        try:
            msg = get_message()
            if msg == "EOF":
                running = False
                break
            elif msg in ("DECODE_ERROR", None):
                continue

            msg_type = msg.get("type")

            if msg_type == "SET_CONFIG":
                new_config = msg.get("config", {})
                with config_lock:
                    config["colors_file"] = os.path.expanduser(new_config.get("colorsPath", "") or "")
                    config["websites_dir"] = os.path.expanduser(new_config.get("websitesDir", "") or "")
                _stored_config_cache = new_config

            elif msg_type == "FETCH_NOW":
                # Force a re-read on next poll cycle by resetting hash
                # We signal this by sending the current data immediately
                with config_lock:
                    colors_file = config["colors_file"]
                    websites_dir = config["websites_dir"]
                if colors_file:
                    data = get_theme_data(colors_file, websites_dir)
                    send_message({"type": "MATUGEN_UPDATE", "data": data})

            elif msg_type == "SAVE_CONFIG":
                config_data = msg.get("config", {})
                try:
                    config_path = get_config_path()
                    atomic_write_json(config_path, config_data)
                    send_message({"type": "SAVE_CONFIG_SUCCESS"})
                    _stored_config_cache = config_data
                except Exception as e:
                    print(f"MatugenFox host error (SAVE_CONFIG): {e}", file=sys.stderr)

            elif msg_type == "GET_CONFIG":
                try:
                    config_path = get_config_path()
                    if os.path.exists(config_path):
                        with open(config_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        send_message({"type": "STORED_CONFIG", "config": data})
                        _stored_config_cache = data
                    else:
                        send_message({"type": "STORED_CONFIG", "config": None})
                except Exception as e:
                    send_message({"type": "STORED_CONFIG", "config": None})

            elif msg_type == "GET_PROFILE_PATHS":
                # Return all discovered Firefox profiles + auto-detected chrome dir
                try:
                    profiles = find_firefox_profiles()
                    auto_chrome = resolve_chrome_dir(_stored_config_cache)
                    send_message({
                        "type": "PROFILE_PATHS",
                        "profiles": profiles,
                        "autoChrome": auto_chrome,
                    })
                except Exception as e:
                    send_message({"type": "PROFILE_PATHS", "profiles": [], "autoChrome": None, "error": str(e)})

            elif msg_type in ("WRITE_USER_CHROME", "WRITE_USER_CONTENT"):
                target = "userChrome" if msg_type == "WRITE_USER_CHROME" else "userContent"
                enabled = msg.get("enabled", False)
                font_size = msg.get("fontSize", 13)
                try:
                    chrome_dir = resolve_chrome_dir(_stored_config_cache)
                    ok, error = write_user_css(target, enabled, chrome_dir, font_size)
                    send_message({
                        "type": "CSS_TOGGLE_RESULT",
                        "target": target,
                        "enabled": enabled,
                        "success": ok,
                        "error": error,
                        "chromeDir": chrome_dir,
                    })
                except Exception as e:
                    send_message({
                        "type": "CSS_TOGGLE_RESULT",
                        "target": target,
                        "enabled": enabled,
                        "success": False,
                        "error": str(e),
                    })

            elif msg_type == "SET_FONT_SIZE":
                font_size = msg.get("fontSize", 13)
                try:
                    chrome_dir = resolve_chrome_dir(_stored_config_cache)
                    ok, error = update_font_size(chrome_dir, font_size)
                    send_message({
                        "type": "FONT_SIZE_RESULT",
                        "success": ok,
                        "fontSize": font_size,
                        "error": error,
                    })
                except Exception as e:
                    send_message({"type": "FONT_SIZE_RESULT", "success": False, "error": str(e)})

        except Exception as e:
            print(f"MatugenFox host error (handler): {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)


def main():
    global running
    threading.Thread(target=message_handler, daemon=True).start()

    last_hash = ""
    last_colors_mtime = -1
    last_websites_state = None

    while running:
        try:
            with config_lock:
                colors_file = config["colors_file"]
                websites_dir = config["websites_dir"]

            if not colors_file:
                time.sleep(2)
                continue

            should_update = False

            # Check colors file mtime (hot-reload trigger)
            current_colors_mtime = -1
            if os.path.exists(colors_file):
                try:
                    current_colors_mtime = os.path.getmtime(colors_file)
                except OSError:
                    pass

            if current_colors_mtime != last_colors_mtime:
                last_colors_mtime = current_colors_mtime
                should_update = True

            # Check websites directory
            current_websites_state = get_dir_state(websites_dir)
            if current_websites_state != last_websites_state:
                last_websites_state = current_websites_state
                should_update = True

            if should_update or not last_hash:
                data = get_theme_data(colors_file, websites_dir)
                current_hash = get_data_hash(data)

                if current_hash != last_hash:
                    last_hash = current_hash
                    data["timestamp"] = time.time()
                    send_message({"type": "MATUGEN_UPDATE", "data": data})

            time.sleep(2)
        except Exception as e:
            print(f"MatugenFox host error (main): {e}", file=sys.stderr)
            time.sleep(5)

    sys.exit(0)


if __name__ == "__main__":
    main()