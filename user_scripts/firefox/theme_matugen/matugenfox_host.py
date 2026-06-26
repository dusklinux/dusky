#!/usr/bin/env python3
import sys
import json
import struct
import os
import time
import re
import hashlib
import threading
import traceback

# --- Global State ---
config = {
    "colors_file": None,
    "websites_dir": None
}
config_lock = threading.Lock()
stdout_lock = threading.Lock()  # Protection against concurrent protocol corruption
running = True

def safe_path(base_dir, filename):
    """Resolve the path safely, permitting symlinks but preventing directory traversal."""
    if not filename or not base_dir:
        return None
    # Strictly ensure the filename contains no path separators.
    # This renders directory traversal impossible while allowing base_dir 
    # and the target file to be legitimate symlinks (e.g., for GNU Stow/Dotfiles).
    if os.path.basename(filename) != filename:
        return None
    # Reject hidden files
    if filename.startswith('.'):
        return None
    
    return os.path.join(base_dir, filename)

def get_config_path():
    """Get a safe, user-writable path for the config file respecting XDG standard."""
    # os.environ.get() yields an empty string if exported but unset. Evaluated via 'or'
    config_dir = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    app_dir = os.path.join(config_dir, 'matugenfox')
    os.makedirs(app_dir, exist_ok=True)
    return os.path.join(app_dir, 'config.json')

def atomic_write(filepath, content):
    """Atomically write string content to prevent TOC/TOU race conditions."""
    temp_path = f"{filepath}.tmp.{os.getpid()}"
    with open(temp_path, 'w', encoding='utf-8') as f:
        f.write(content)
    os.replace(temp_path, filepath)

def atomic_write_json(filepath, data):
    """Atomically write JSON content."""
    temp_path = f"{filepath}.tmp.{os.getpid()}"
    with open(temp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    os.replace(temp_path, filepath)

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

def get_message():
    """Read a message precisely adhering to the Native Messaging protocol."""
    raw_length = sys.stdin.buffer.read(4)
    if len(raw_length) == 0:
        return "EOF"
    if len(raw_length) < 4:
        return None
        
    message_length = struct.unpack('=I', raw_length)[0]
    if message_length > 10 * 1024 * 1024: # 10MB sanity limit
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
                
                # Robust bracket-counting state machine to prevent nested brace failures
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
                            # Handle string escapes to prevent premature termination
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
                        websites[domain] = content[start_idx+1 : end_idx].strip()
                    else:
                        websites[domain] = content[start_idx+1:].strip() # Malformed CSS fallback
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

def message_handler():
    global config, running
    while running:
        try:
            msg = get_message()
            if msg == "EOF":
                running = False
                break
            elif msg in ("DECODE_ERROR", None):
                continue
            
            if msg.get("type") == "SET_CONFIG":
                new_config = msg.get("config", {})
                with config_lock:
                    config["colors_file"] = os.path.expanduser(new_config.get("colorsPath", ""))
                    config["websites_dir"] = os.path.expanduser(new_config.get("websitesDir", ""))
            
            elif msg.get("type") == "LIST_WEBSITES":
                try:
                    with config_lock:
                        wdir = config["websites_dir"]
                    if wdir and os.path.isdir(wdir):
                        files = [f for f in os.listdir(wdir) if f.endswith(".css")]
                        send_message({"type": "WEBSITE_LIST", "files": sorted(files)})
                    else:
                        send_message({"type": "WEBSITE_LIST", "files": []})
                except Exception as e:
                    send_message({"type": "WEBSITE_LIST", "files": []})

            elif msg.get("type") == "READ_WEBSITE_CSS":
                filename = msg.get("filename")
                try:
                    with config_lock:
                        wdir = config["websites_dir"]
                    path = safe_path(wdir, filename)
                    if path and os.path.isfile(path):
                        with open(path, 'r', encoding='utf-8') as f:
                            send_message({"type": "WEBSITE_CSS", "filename": filename, "content": f.read()})
                    else:
                        send_message({"type": "WEBSITE_CSS", "filename": filename, "content": ""})
                except Exception as e:
                    print(f"MatugenFox host error (READ_WEBSITE_CSS): {e}", file=sys.stderr)
                    send_message({"type": "WEBSITE_CSS", "filename": filename, "content": ""})

            elif msg.get("type") == "SAVE_CONFIG":
                config_data = msg.get("config", {})
                try:
                    config_path = get_config_path()
                    atomic_write_json(config_path, config_data)
                    send_message({"type": "SAVE_CONFIG_SUCCESS"})
                except Exception as e:
                    print(f"MatugenFox host error (SAVE_CONFIG): {e}", file=sys.stderr)

            elif msg.get("type") == "GET_CONFIG":
                try:
                    config_path = get_config_path()
                    if os.path.exists(config_path):
                        with open(config_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        send_message({"type": "STORED_CONFIG", "config": data})
                    else:
                        send_message({"type": "STORED_CONFIG", "config": None})
                except Exception as e:
                    send_message({"type": "STORED_CONFIG", "config": None})

            elif msg.get("type") == "SAVE_WEBSITE_CSS":
                filename = msg.get("filename")
                content = msg.get("content")
                try:
                    with config_lock:
                        wdir = config["websites_dir"]
                    path = safe_path(wdir, filename)
                    if path and content is not None:
                        atomic_write(path, content)
                        send_message({"type": "SAVE_SUCCESS", "filename": filename})
                except Exception as e:
                    print(f"MatugenFox host error (SAVE_WEBSITE_CSS): {e}", file=sys.stderr)
                    
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
            
            # Check colors file (Tracks creations, updates, and restorations via strict inequality)
            current_colors_mtime = -1
            if os.path.exists(colors_file):
                try:
                    current_colors_mtime = os.path.getmtime(colors_file)
                except OSError:
                    pass
            
            if current_colors_mtime != last_colors_mtime:
                last_colors_mtime = current_colors_mtime
                should_update = True
            
            # Check websites directory (Composite map tracks inner deletions and restorations)
            current_websites_state = get_dir_state(websites_dir)
            if current_websites_state != last_websites_state:
                last_websites_state = current_websites_state
                should_update = True
            
            if should_update or not last_hash:
                data = get_theme_data(colors_file, websites_dir)
                current_hash = get_data_hash(data)
                
                # Protects against redundant websocket payloads
                if current_hash != last_hash:
                    last_hash = current_hash
                    data["timestamp"] = time.time()
                    send_message(data)
            
            time.sleep(2)
        except Exception as e:
            print(f"MatugenFox host error (main): {e}", file=sys.stderr)
            time.sleep(5)
            
    sys.exit(0)

if __name__ == "__main__":
    main()
