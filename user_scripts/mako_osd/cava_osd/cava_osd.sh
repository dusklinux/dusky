#!/usr/bin/env bash
# ==============================================================================
# DUSKY CAVA - MAKO OSD VISUALIZER
# ==============================================================================

set -euo pipefail

readonly APP_NAME="dusky-cava"
readonly LOCK_FILE="/tmp/${APP_NAME}.lock"
readonly PID_FILE="/tmp/${APP_NAME}.pid"
readonly CAVA_CONF="/tmp/${APP_NAME}_cava.conf"

LOCK_FD=""
LOCK_HELD=false

notify_user() {
    if command -v notify-send >/dev/null 2>&1; then
        notify-send -u low -t 2000 --app-name="dusky-cava-alert" "Dusky Cava" "$1" || true
    fi
}

acquire_lock() {
    if ! exec {LOCK_FD}> "$LOCK_FILE"; then exit 1; fi
    
    # TOGGLE LOGIC: If it's already running, kill it and exit
    if ! flock -n "$LOCK_FD"; then
        if [[ -f "$PID_FILE" ]]; then
            pid=$(cat "$PID_FILE")
            kill -TERM "$pid" 2>/dev/null || true
            rm -f "$PID_FILE" 2>/dev/null || true
        fi
        
        # Clear any lingering visualizer pill from the screen
        notify-send -a "$APP_NAME" -h string:x-canonical-private-synchronous:"$APP_NAME-sync" -t 1 " " || true
        notify_user "Visualizer Disabled"
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
    if [[ -n "${PYTHON_PID:-}" ]]; then
        kill -TERM "$PYTHON_PID" 2>/dev/null || true
    fi
    rm -f "$PID_FILE" "$CAVA_CONF" 2>/dev/null || true
    notify-send -a "$APP_NAME" -h string:x-canonical-private-synchronous:"$APP_NAME-sync" -t 1 " " || true
    release_lock
}
trap cleanup EXIT INT TERM

# --- 1. DEPENDENCY CHECK ---
if ! command -v cava >/dev/null 2>&1; then
    notify_user "Error: 'cava' is not installed."
    exit 1
fi

acquire_lock

# --- 2. GENERATE CAVA CONFIG ---
# 15 FPS is the sweet spot. Smooth enough to look good, low enough to not crash DBus.
cat > "$CAVA_CONF" << 'EOF'
[general]
framerate = 15
bars = 16

[output]
method = raw
raw_target = /dev/stdout
data_format = ascii
ascii_max_range = 7
EOF

# --- 3. PYTHON ASYNC RUNNER ---
# This reads Cava's output and updates Mako asynchronously without blocking.
python3 -c '
import asyncio
import sys

CHARS = [" ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
APP_NAME = "dusky-cava"
SYNC_ID = f"{APP_NAME}-sync"

async def main():
    # Start Cava as a subprocess
    cava_proc = await asyncio.create_subprocess_exec(
        "cava", "-p", "'"$CAVA_CONF"'",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL
    )
    
    idle_frames = 0
    is_hidden = False

    try:
        while True:
            line = await cava_proc.stdout.readline()
            if not line:
                break
                
            raw_data = line.decode("ascii").strip()
            if not raw_data: continue
            
            # Parse the raw ascii values (e.g., "0;4;7;2;")
            values = [v for v in raw_data.split(";") if v.isdigit()]
            
            # Auto-hide logic: If all bars are 0 for ~1.5 seconds (20 frames at 15fps)
            if all(v == "0" for v in values):
                idle_frames += 1
            else:
                idle_frames = 0
                is_hidden = False

            if idle_frames == 20:
                # Send a 1ms empty notification to wipe it from the screen
                proc = await asyncio.create_subprocess_exec(
                    "notify-send", "-a", APP_NAME, 
                    "-h", f"string:x-canonical-private-synchronous:{SYNC_ID}", 
                    "-t", "1", " "
                )
                await proc.wait()
                is_hidden = True
            
            elif idle_frames < 20 and not is_hidden:
                # Construct the block characters
                text = "".join(CHARS[int(v)] for v in values)
                
                # Send the update to Mako
                proc = await asyncio.create_subprocess_exec(
                    "notify-send", "-a", APP_NAME, 
                    "-h", f"string:x-canonical-private-synchronous:{SYNC_ID}", 
                    "-t", "2000", text
                )
                await proc.wait()

    except asyncio.CancelledError:
        cava_proc.terminate()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
' &

PYTHON_PID="$!"
echo "$PYTHON_PID" > "$PID_FILE"

notify_user "Visualizer Enabled"

wait "$PYTHON_PID"
