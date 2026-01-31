#!/usr/bin/env bash
# waybar-net: Minimal JSON output for Waybar

STATE_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/waybar-net"
STATE_FILE="$STATE_DIR/state"
HEARTBEAT_FILE="$STATE_DIR/heartbeat"
PID_FILE="$STATE_DIR/daemon.pid"

# Defaults
UNIT="-" UP="-" DOWN="-" CLASS="network-disconnected"

# Read state (fast: tmpfs)
[[ -r "$STATE_FILE" ]] && read -r UNIT UP DOWN CLASS < "$STATE_FILE"

# Signal daemon via heartbeat
mkdir -p "$STATE_DIR"
touch "$HEARTBEAT_FILE"
[[ -r "$PID_FILE" ]] && kill -USR1 "$(<"$PID_FILE")" 2>/dev/null || true

# Fixed-width formatter (3 chars)
fmt() {
    local s="${1:--}" len=${#1}
    if (( len == 1 )); then printf ' %s ' "$s"
    elif (( len == 2 )); then printf ' %s' "$s"
    else printf '%.3s' "$s"
    fi
}

D_UNIT=$(fmt "$UNIT")
D_UP=$(fmt "$UP")
D_DOWN=$(fmt "$DOWN")

# Tooltip
if [[ "$CLASS" == "network-disconnected" ]]; then
    TT="Disconnected"
else
    TT="Upload: ${UP} ${UNIT}/s\\nDownload: ${DOWN} ${UNIT}/s"
fi

# Output
case "${1:-}" in
    --vertical|vertical)   TEXT="${D_UP}\\n${D_UNIT}\\n${D_DOWN}" ;;
    --horizontal|horizontal) TEXT="${D_UP} ${D_UNIT} ${D_DOWN}" ;;
    unit)       TEXT="$D_UNIT" ;;
    up|upload)  TEXT="$D_UP" ;;
    down|download) TEXT="$D_DOWN" ;;
    *)          printf '{}\n'; exit 0 ;;
esac

printf '{"text":"%s","class":"%s","tooltip":"%s"}\n' "$TEXT" "$CLASS" "$TT"
