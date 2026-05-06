#!/usr/bin/env bash
# ==============================================================================
# DUSKY KEYS ELITE - ARCH LINUX / UV OPTIMIZED
# ==============================================================================
# Global Keystroke Visualizer leveraging native uvloop and asynchronous polling.
# ==============================================================================

set -euo pipefail
shopt -s inherit_errexit

RUN_MODE="run"
case "${1:-}" in
    --reset) RUN_MODE="reset" ;;
    --setup) RUN_MODE="setup" ;;
    --help|-h)
        printf "Usage: %s [--setup|--reset]\n" "$(basename -- "$0")"
        exit 0
        ;;
    "") ;;
    *) exit 1 ;;
esac

# ╔════════════════════════════════════════════════════════════════════════════╗
# ║  INTERNAL CONFIGURATION                                                    ║
# ╚════════════════════════════════════════════════════════════════════════════╝

readonly APP_NAME="dusky_keys"
readonly BASE_DIR="$HOME/contained_apps/uv/$APP_NAME"
readonly VENV_DIR="$BASE_DIR/.venv"
readonly PYTHON_BIN="$VENV_DIR/bin/python"
readonly RUNNER_SCRIPT="$BASE_DIR/runner.py"
readonly PID_FILE="$BASE_DIR/$APP_NAME.pid"
readonly LOCK_FILE="$BASE_DIR/$APP_NAME.lock"
readonly MARKER_FILE="$BASE_DIR/.build_marker_v1"

# --- ANSI COLORS ---
readonly C_RED=$'\033[1;31m'
readonly C_GREEN=$'\033[1;32m'
readonly C_BLUE=$'\033[1;34m'
readonly C_CYAN=$'\033[1;36m'
readonly C_YELLOW=$'\033[1;33m'
readonly C_DIM=$'\033[2m'
readonly C_RESET=$'\033[0m'

RUNNER_CHILD_PID=""
LOCK_FD=""
LOCK_HELD=false

# --- UTILITY FUNCTIONS ---

notify_user() {
    if command -v notify-send >/dev/null 2>&1; then
        notify-send -u critical -t 5000 --app-name="dusky-keys" "Dusky Keys" "$1" || true
    fi
}

acquire_lock() {
    mkdir -p "$BASE_DIR" 2>/dev/null || true
    if ! exec {LOCK_FD}> "$LOCK_FILE"; then exit 1; fi
    
    # TOGGLE LOGIC: If we can't acquire the lock, the daemon is already running.
    if ! flock -n "$LOCK_FD"; then
        printf "%b[TOGGLE]%b Dusky Keys is running. Shutting it down...\n" "${C_YELLOW}" "${C_RESET}"
        if [[ -f "$PID_FILE" ]]; then
            local pid
            pid=$(cat "$PID_FILE")
            kill -TERM "$pid" 2>/dev/null || true
            rm -f "$PID_FILE" 2>/dev/null || true
        fi
        if command -v notify-send >/dev/null 2>&1; then
            notify-send -u low -t 2000 --app-name="dusky-keys" "Dusky Keys" "Visualizer Disabled" || true
        fi
        exit 0
    fi
    LOCK_HELD=true
}

release_lock() {
    [[ "$LOCK_HELD" == true ]] || return 0
    flock -u "$LOCK_FD" 2>/dev/null || true
    exec {LOCK_FD}>&- || true
}

cleanup() {
    if [[ -n "${RUNNER_CHILD_PID:-}" ]]; then
        kill -TERM "$RUNNER_CHILD_PID" 2>/dev/null || true
    fi
    rm -f "$PID_FILE" 2>/dev/null || true
    release_lock
}
trap cleanup EXIT INT TERM

current_session_has_input_access() {
    if id -nG | grep -qw -- input; then return 0; fi
    shopt -s nullglob
    for node in /dev/input/event*; do
        if [[ -r "$node" ]]; then shopt -u nullglob; return 0; fi
    done
    shopt -u nullglob
    return 1
}

# --- 1. RESET MODE ---
if [[ "$RUN_MODE" == "reset" ]]; then
    printf "%b[RESET]%b Cleaning Dusky Keys environment...\n" "${C_BLUE}" "${C_RESET}"
    if [[ -f "$PID_FILE" ]]; then
        pid=$(cat "$PID_FILE")
        kill -TERM "$pid" 2>/dev/null || true
    fi
    rm -rf "$BASE_DIR"
    printf "%b[SUCCESS]%b Environment deleted.\n" "${C_GREEN}" "${C_RESET}"
    exit 0
fi

# --- 2. INPUT ACCESS CHECK ---
if ! current_session_has_input_access; then
    printf "%b[CRITICAL]%b You are not in the 'input' group.\n" "${C_RED}" "${C_RESET}"
    printf "Run: %bsudo usermod -aG input %s%b\n" "${C_CYAN}" "$USER" "${C_RESET}"
    notify_user "Permission Denied. Run: sudo usermod -aG input $USER\nThen log out and log back in."
    exit 1
fi

acquire_lock

# --- 3. DEPENDENCY & VENV SETUP ---
mkdir -p "$BASE_DIR" 2>/dev/null || true

if [[ ! -x "$PYTHON_BIN" ]]; then
    printf "%b[BUILD]%b Initializing UV environment...\n" "${C_BLUE}" "${C_RESET}"
    if ! command -v uv >/dev/null 2>&1; then
        notify_user "Missing 'uv' package manager. Install it first."
        exit 1
    fi
    uv venv "$VENV_DIR" --quiet
fi

if [[ ! -f "$MARKER_FILE" ]]; then
    printf "%b[BUILD]%b Compiling python dependencies with native CPU flags...\n" "${C_YELLOW}" "${C_RESET}"
    export CFLAGS="-march=native -O3 -pipe -flto=auto"
    uv pip install --python "$PYTHON_BIN" --upgrade --no-binary evdev evdev
    uv pip install --python "$PYTHON_BIN" --upgrade --no-binary uvloop uvloop || true
    touch "$MARKER_FILE"
    printf "%b[SUCCESS]%b Native build complete.\n" "${C_GREEN}" "${C_RESET}"
fi

# --- 4. PYTHON RUNNER GENERATION ---
cat > "$RUNNER_SCRIPT" << 'PYTHON_EOF'
import asyncio
import os
import signal
import sys
from evdev import InputDevice, ecodes, list_devices

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

SYNC_ID = "dusky-keys-sync"
APP_NAME = "dusky-keys"
DISPLAY_TIMEOUT = 3.0
POLL_INTERVAL = 1.0

_shift_pressed = False
_caps_active = False
_key_buffer = []
_clear_task = None

KEYMAP = {
    ecodes.KEY_A: ('a', 'A'), ecodes.KEY_B: ('b', 'B'), ecodes.KEY_C: ('c', 'C'),
    ecodes.KEY_D: ('d', 'D'), ecodes.KEY_E: ('e', 'E'), ecodes.KEY_F: ('f', 'F'),
    ecodes.KEY_G: ('g', 'G'), ecodes.KEY_H: ('h', 'H'), ecodes.KEY_I: ('i', 'I'),
    ecodes.KEY_J: ('j', 'J'), ecodes.KEY_K: ('k', 'K'), ecodes.KEY_L: ('l', 'L'),
    ecodes.KEY_M: ('m', 'M'), ecodes.KEY_N: ('n', 'N'), ecodes.KEY_O: ('o', 'O'),
    ecodes.KEY_P: ('p', 'P'), ecodes.KEY_Q: ('q', 'Q'), ecodes.KEY_R: ('r', 'R'),
    ecodes.KEY_S: ('s', 'S'), ecodes.KEY_T: ('t', 'T'), ecodes.KEY_U: ('u', 'U'),
    ecodes.KEY_V: ('v', 'V'), ecodes.KEY_W: ('w', 'W'), ecodes.KEY_X: ('x', 'X'),
    ecodes.KEY_Y: ('y', 'Y'), ecodes.KEY_Z: ('z', 'Z'),
    ecodes.KEY_1: ('1', '!'), ecodes.KEY_2: ('2', '@'), ecodes.KEY_3: ('3', '#'),
    ecodes.KEY_4: ('4', '$'), ecodes.KEY_5: ('5', '%'), ecodes.KEY_6: ('6', '^'),
    ecodes.KEY_7: ('7', '&'), ecodes.KEY_8: ('8', '*'), ecodes.KEY_9: ('9', '('),
    ecodes.KEY_0: ('0', ')'),
    ecodes.KEY_MINUS: ('-', '_'), ecodes.KEY_EQUAL: ('=', '+'),
    ecodes.KEY_LEFTBRACE: ('[', '{'), ecodes.KEY_RIGHTBRACE: (']', '}'),
    ecodes.KEY_BACKSLASH: ('\\', '|'), ecodes.KEY_SEMICOLON: (';', ':'),
    ecodes.KEY_APOSTROPHE: ("'", '"'), ecodes.KEY_GRAVE: ('`', '~'),
    ecodes.KEY_COMMA: (',', '<'), ecodes.KEY_DOT: ('.', '>'), ecodes.KEY_SLASH: ('/', '?'),
    ecodes.KEY_SPACE: (' ', ' '),
}

MODIFIERS = {
    ecodes.KEY_LEFTSHIFT: "⇧", ecodes.KEY_RIGHTSHIFT: "⇧",
    ecodes.KEY_LEFTCTRL: "Ctrl", ecodes.KEY_RIGHTCTRL: "Ctrl",
    ecodes.KEY_LEFTALT: "Alt", ecodes.KEY_RIGHTALT: "Alt",
    ecodes.KEY_LEFTMETA: "Super", ecodes.KEY_RIGHTMETA: "Super",
    ecodes.KEY_TAB: "⇥", ecodes.KEY_ENTER: "↵", ecodes.KEY_BACKSPACE: "⌫",
    ecodes.KEY_ESC: "Esc"
}

async def update_display():
    display_text = "".join(_key_buffer)
    if not display_text: return
    proc = await asyncio.create_subprocess_exec(
        "notify-send", "-a", APP_NAME,
        "-h", f"string:x-canonical-private-synchronous:{SYNC_ID}",
        "-t", "3000", display_text,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()

async def clear_buffer_after_delay():
    global _key_buffer
    await asyncio.sleep(DISPLAY_TIMEOUT)
    _key_buffer.clear()
    proc = await asyncio.create_subprocess_exec(
        "notify-send", "-a", APP_NAME,
        "-h", f"string:x-canonical-private-synchronous:{SYNC_ID}",
        "-t", "1", " ",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()

def process_keystroke(event):
    global _shift_pressed, _caps_active, _clear_task, _key_buffer
    if event.code in (ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT):
        _shift_pressed = (event.value in (1, 2))
        return
    if event.value != 1: return
    if event.code == ecodes.KEY_CAPSLOCK:
        _caps_active = not _caps_active
        return

    char = ""
    if event.code in KEYMAP:
        base, shifted = KEYMAP[event.code]
        if base.isalpha(): char = shifted if (_shift_pressed ^ _caps_active) else base
        else: char = shifted if _shift_pressed else base
    elif event.code in MODIFIERS:
        char = f" [{MODIFIERS[event.code]}] "
    else: return

    _key_buffer.append(char)
    if len(_key_buffer) > 8: _key_buffer.pop(0)

    if _clear_task and not _clear_task.done(): _clear_task.cancel()
    _clear_task = asyncio.create_task(clear_buffer_after_delay())
    asyncio.create_task(update_display())

async def read_device(dev: InputDevice, stop: asyncio.Event):
    try:
        async for event in dev.async_read_loop():
            if stop.is_set(): break
            if event.type == ecodes.EV_KEY: process_keystroke(event)
    except OSError: pass
    finally:
        try: dev.close()
        except OSError: pass

def scan_devices(monitored_tasks, stop):
    dead_paths = [p for p, t in monitored_tasks.items() if t.done()]
    for p in dead_paths: monitored_tasks.pop(p)

    for path in list_devices():
        if path in monitored_tasks: continue
        try:
            dev = InputDevice(path)
            caps = dev.capabilities()
            if ecodes.EV_KEY in caps and ecodes.KEY_ENTER in caps.get(ecodes.EV_KEY, []):
                monitored_tasks[path] = asyncio.create_task(read_device(dev, stop))
            else: dev.close()
        except OSError: pass

async def main():
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    
    def _stop(): loop.call_soon_threadsafe(stop.set)
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _stop)

    monitored_tasks = {}
    print("Dusky Keys engine started...")
    
    try:
        scan_devices(monitored_tasks, stop)
        while not stop.is_set():
            try: await asyncio.wait_for(stop.wait(), timeout=POLL_INTERVAL)
            except asyncio.TimeoutError: scan_devices(monitored_tasks, stop)
    finally:
        for task in monitored_tasks.values(): task.cancel()

if __name__ == "__main__":
    asyncio.run(main())
PYTHON_EOF

# --- 5. EXECUTION ---
if [[ "$RUN_MODE" == "setup" ]]; then
    printf "%b[SUCCESS]%b Setup complete.\n" "${C_GREEN}" "${C_RESET}"
    exit 0
fi

printf "%b[RUN]%b Starting Dusky Keys background daemon...\n" "${C_BLUE}" "${C_RESET}"
"$PYTHON_BIN" -OO -B "$RUNNER_SCRIPT" &
RUNNER_CHILD_PID="$!"
echo "$RUNNER_CHILD_PID" > "$PID_FILE"

# Send a notification so the user knows it successfully started
if command -v notify-send >/dev/null 2>&1; then
    notify-send -u low -t 2000 --app-name="dusky-keys" "Dusky Keys" "Visualizer Enabled" || true
fi

wait "$RUNNER_CHILD_PID"
