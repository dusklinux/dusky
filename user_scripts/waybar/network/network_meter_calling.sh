#!/usr/bin/env bash
# waybar-net: Minimal JSON output for Waybar (Zero-Fork Edition)

# 1. OPTIMIZATION: Use ${UID} (Bash variable) instead of $(id -u) (Process fork)
STATE_DIR="${XDG_RUNTIME_DIR:-/run/user/${UID}}/waybar-net"
STATE_FILE="$STATE_DIR/state"
HEARTBEAT_FILE="$STATE_DIR/heartbeat"
PID_FILE="$STATE_DIR/daemon.pid"

# Defaults
UNIT="-" UP="-" DOWN="-" CLASS="network-disconnected"

# Read state (fast: tmpfs)
# Added "|| true" to prevent exit on read failure if file is being rotated
[[ -r "$STATE_FILE" ]] && read -r UNIT UP DOWN CLASS < "$STATE_FILE" || true

# Signal daemon via heartbeat
mkdir -p "$STATE_DIR"
touch "$HEARTBEAT_FILE"

# OPTIMIZATION: Only kill if PID file exists and process is actually running
if [[ -r "$PID_FILE" ]]; then
    read -r DAEMON_PID < "$PID_FILE"
    # 0 signal checks if process exists without killing it
    if [[ -n "$DAEMON_PID" ]] && kill -0 "$DAEMON_PID" 2>/dev/null; then
        kill -USR1 "$DAEMON_PID" 2>/dev/null || true
    fi
fi

# Formatter for Horizontal Mode (Original Unpadded Behavior)
fmt_h() {
    local s="${1:--}"
    local len="${#s}"
    
    if (( len == 1 )); then printf ' %s ' "$s"
    elif (( len == 2 )); then printf ' %s' "$s"
    else printf '%.3s' "$s"
    fi
}

# Formatter for Vertical Mode (Strict alignment matching update_counter.sh)
fmt_v() {
    local s="${1:--}"
    # CRITICAL FIX: Evaluated on a separate line to prevent Bash expansion zeroing
    local len="${#s}" 
    
    if (( len >= 3 )); then
        printf '%.3s' "$s"
    elif (( len == 2 )); then
        # Natively pass literal JSON unicode escape so Waybar parses it perfectly
        printf '\\u2005%s\\u2005' "$s"
    elif (( len == 1 )); then
        printf ' %s ' "$s"
    else
        printf '   '
    fi
}

# Tooltip
if [[ "$CLASS" == "network-disconnected" ]]; then
    TT="Disconnected"
else
    TT="Upload: ${UP} ${UNIT}/s\\nDownload: ${DOWN} ${UNIT}/s"
fi

# Output Selection
case "${1:-}" in
    --vertical|vertical)     
        TEXT="$(fmt_v "$UP")\\n$(fmt_v "$UNIT")\\n$(fmt_v "$DOWN")" 
        ;;
    --horizontal|horizontal) 
        TEXT="$(fmt_h "$UP") $(fmt_h "$UNIT") $(fmt_h "$DOWN")" 
        ;;
    unit)                    TEXT="$(fmt_h "$UNIT")" ;;
    up|upload)               TEXT="$(fmt_h "$UP")" ;;
    down|download)           TEXT="$(fmt_h "$DOWN")" ;;
    *)                       printf '{}\n'; exit 0 ;;
esac

printf '{"text":"%s","class":"%s","tooltip":"%s"}\n' "$TEXT" "$CLASS" "$TT"
