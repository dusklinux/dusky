#!/usr/bin/env bash
# shellcheck disable=SC2154
STATE_DIR="${XDG_RUNTIME_DIR:-/run/user/${UID:-$(id -u)}}/waybar-net"
STATE_FILE="$STATE_DIR/state"
HEARTBEAT_FILE="$STATE_DIR/heartbeat"
PID_FILE="$STATE_DIR/daemon.pid"
UNIT="-" UP="-" DOWN="-" CLASS="network-disconnected"
for ((_try=0; _try<5; _try++)); do
    if [[ -r "$STATE_FILE" ]] && read -r _u _up _down _c < "$STATE_FILE" 2>/dev/null && [[ -n "${_c:-}" ]]; then
        case "$_u" in
            KB|MB|GB|-)
                UNIT="$_u"; UP="$_up"; DOWN="$_down"; CLASS="$_c"
                break
                ;;
        esac
    fi
done
[[ -d "$STATE_DIR" ]] || command mkdir -p "$STATE_DIR" 2>/dev/null
: > "$HEARTBEAT_FILE" 2>/dev/null
if [[ -r "$PID_FILE" ]]; then
    read -r DAEMON_PID < "$PID_FILE" 2>/dev/null || DAEMON_PID=""
    case "$DAEMON_PID" in
        ""|*[!0-9]*) ;;
        *)
            if kill -0 "$DAEMON_PID" 2>/dev/null; then
                _verified=0
                if exec {_pfd}< "/proc/$DAEMON_PID/cmdline" 2>/dev/null; then
                    IFS= read -r -d '' _c1 <&"$_pfd" 2>/dev/null || _c1=""
                    IFS= read -r -d '' _c2 <&"$_pfd" 2>/dev/null || _c2=""
                    exec {_pfd}<&- 2>/dev/null
                    [[ "$_c2" == *network_meter_daemon* ]] && _verified=1
                fi
                (( _verified )) && kill -USR1 "$DAEMON_PID" 2>/dev/null
            fi
            ;;
    esac
fi
fmt_h() {
    local -n _out=$1
    local s="${2:--}"
    local len="${#s}"
    if (( len == 1 )); then _out=" $s "
    elif (( len == 2 )); then _out=" $s"
    elif (( len >= 3 )); then _out="${s:0:3}"
    else _out="   "
    fi
}
fmt_v() {
    local -n _out=$1
    local s="${2:--}"
    local len="${#s}"
    if (( len >= 3 )); then
        _out="${s:0:3}"
    elif (( len == 2 )); then
        _out=" ${s}"
    elif (( len == 1 )); then
        _out=" ${s} "
    else
        _out="   "
    fi
}
if [[ "$CLASS" == "network-disconnected" ]]; then
    TT="Disconnected"
else
    TT="Upload: ${UP} ${UNIT}/s\\nDownload: ${DOWN} ${UNIT}/s"
fi
case "${1:-}" in
    --vertical|vertical)
        fmt_v up_fmt "$UP"
        fmt_v unit_fmt "$UNIT"
        fmt_v down_fmt "$DOWN"
        TEXT="${up_fmt}\\n${unit_fmt}\\n${down_fmt}"
        ;;
    unit)
        fmt_h unit_fmt "$UNIT"
        TEXT="$unit_fmt"
        ;;
    up|upload)
        fmt_h up_fmt "$UP"
        TEXT="$up_fmt"
        ;;
    down|download)
        fmt_h down_fmt "$DOWN"
        TEXT="$down_fmt"
        ;;
    *)
        fmt_h up_fmt "$UP"
        fmt_h unit_fmt "$UNIT"
        fmt_h down_fmt "$DOWN"
        TEXT="${up_fmt} ${unit_fmt} ${down_fmt}"
        ;;
esac
printf '{"text":"%s","class":"%s","tooltip":"%s"}\n' "$TEXT" "$CLASS" "$TT"
