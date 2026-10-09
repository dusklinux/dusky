#!/usr/bin/env bash
# shellcheck disable=SC2154
STATE_DIR="${XDG_RUNTIME_DIR:-/run/user/${UID:-$(id -u)}}/waybar-net"
STATE_FILE="$STATE_DIR/state"
STATE_EXT_FILE="$STATE_DIR/state_ext"
HEARTBEAT_FILE="$STATE_DIR/heartbeat"
PID_FILE="$STATE_DIR/daemon.pid"
CACHE_DIR="$STATE_DIR/cache"

ARG="${1:-horizontal}"
case "$ARG" in
    --vertical|vertical) FLAG="vertical" ;;
    unit) FLAG="unit" ;;
    up|upload) FLAG="up" ;;
    down|download) FLAG="down" ;;
    combined|speed|rate) FLAG="combined" ;;
    session-down|down-session) FLAG="session-down" ;;
    session-up|up-session) FLAG="session-up" ;;
    session-total|total-session|session) FLAG="session-total" ;;
    boot-down|down-boot|total-down) FLAG="boot-down" ;;
    boot-up|up-boot|total-up) FLAG="boot-up" ;;
    boot-total|boot|total|total-traffic) FLAG="boot-total" ;;
    --horizontal|horizontal|*) FLAG="horizontal" ;;
esac

# Keep the fast stat builtin. The loadable mkdir tries to chmod existing
# ancestors with -p (including /run), so use GNU mkdir for directory creation.
if [[ -f /usr/lib/bash/stat ]]; then
    enable -f /usr/lib/bash/stat stat 2>/dev/null || true
fi

[[ -d "$STATE_DIR" ]] || command mkdir -p "$STATE_DIR" 2>/dev/null
: > "$HEARTBEAT_FILE" 2>/dev/null

CACHE_FILE="$CACHE_DIR/$FLAG.json"
now=$EPOCHSECONDS
cache_fresh=0
if [[ -f "$CACHE_FILE" && -f "$STATE_FILE" ]] && ! [[ "$STATE_FILE" -nt "$CACHE_FILE" ]]; then
    declare -A S_C
    if stat -A S_C "$CACHE_FILE" 2>/dev/null; then
        (( now - S_C[mtime] <= 1 )) && cache_fresh=1
    else
        c_mtime=$(stat -c %Y "$CACHE_FILE" 2>/dev/null || echo 0)
        (( now - c_mtime <= 1 )) && cache_fresh=1
    fi
    if (( cache_fresh == 1 )); then
        if IFS= read -r _cached < "$CACHE_FILE" 2>/dev/null; then
            printf '%s\n' "$_cached"
            exit 0
        fi
    fi
fi

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

TOTAL_SPEED_FMT="" SESSION_DOWN_FMT="" SESSION_UP_FMT="" SESSION_TOTAL_FMT=""
BOOT_DOWN_FMT="" BOOT_UP_FMT="" BOOT_TOTAL_FMT=""
S_RX_U="MB" S_TX_U="MB" S_TOT_U="MB" B_RX_U="MB" B_TX_U="MB" B_TOT_U="GB"
if [[ -r "$STATE_EXT_FILE" ]]; then
    read -r _rx_f _tx_f _tot_f _s_rx _s_tx _s_tot _b_rx _b_tx _b_tot _ext_c _if _s_rx_u _s_tx_u _s_tot_u _b_rx_u _b_tx_u _b_tot_u _ < "$STATE_EXT_FILE" 2>/dev/null || true
    TOTAL_SPEED_FMT="${_tot_f:-}"
    SESSION_DOWN_FMT="${_s_rx:-}"
    SESSION_UP_FMT="${_s_tx:-}"
    SESSION_TOTAL_FMT="${_s_tot:-}"
    BOOT_DOWN_FMT="${_b_rx:-}"
    BOOT_UP_FMT="${_b_tx:-}"
    BOOT_TOTAL_FMT="${_b_tot:-}"
    S_RX_U="${_s_rx_u:-MB}"
    S_TX_U="${_s_tx_u:-MB}"
    S_TOT_U="${_s_tot_u:-MB}"
    B_RX_U="${_b_rx_u:-MB}"
    B_TX_U="${_b_tx_u:-MB}"
    B_TOT_U="${_b_tot_u:-GB}"
fi

state_stale=1
declare -A S_S
if stat -A S_S "$STATE_FILE" 2>/dev/null; then
    (( now - S_S[mtime] <= 2 )) && state_stale=0
else
    s_mtime=$(stat -c %Y "$STATE_FILE" 2>/dev/null || echo 0)
    (( now - s_mtime <= 2 )) && state_stale=0
fi

if (( state_stale == 1 )); then
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
    else
        systemctl --user start network_meter.service 2>/dev/null || true
    fi
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
    if [[ -n "$BOOT_TOTAL_FMT" ]]; then
        TT="Upload: ${UP} ${UNIT}/s (Session: ${SESSION_UP_FMT:-0} ${S_TX_U} | Boot: ${BOOT_UP_FMT:-0} ${B_TX_U})\nDownload: ${DOWN} ${UNIT}/s (Session: ${SESSION_DOWN_FMT:-0} ${S_RX_U} | Boot: ${BOOT_DOWN_FMT:-0} ${B_RX_U})\nTotal Traffic: Session ${SESSION_TOTAL_FMT:-0} ${S_TOT_U} | Boot ${BOOT_TOTAL_FMT:-0} ${B_TOT_U}"
    elif [[ -n "$SESSION_TOTAL_FMT" ]]; then
        TT="Upload: ${UP} ${UNIT}/s (Session: ${SESSION_UP_FMT:-0} ${S_TX_U})\nDownload: ${DOWN} ${UNIT}/s (Session: ${SESSION_DOWN_FMT:-0} ${S_RX_U})\nTotal Session: ${SESSION_TOTAL_FMT:-0} ${S_TOT_U}"
    else
        TT="Upload: ${UP} ${UNIT}/s\\nDownload: ${DOWN} ${UNIT}/s"
    fi
fi
case "$FLAG" in
    vertical)
        fmt_v up_fmt "$UP"
        fmt_v unit_fmt "$UNIT"
        fmt_v down_fmt "$DOWN"
        TEXT="${up_fmt}\\n${unit_fmt}\\n${down_fmt}"
        ;;
    unit)
        fmt_h unit_fmt "$UNIT"
        TEXT="$unit_fmt"
        ;;
    up)
        fmt_h up_fmt "$UP"
        TEXT="$up_fmt"
        ;;
    down)
        fmt_h down_fmt "$DOWN"
        TEXT="$down_fmt"
        ;;
    combined)
        if [[ -n "$TOTAL_SPEED_FMT" ]]; then
            TEXT="$TOTAL_SPEED_FMT"
        else
            fmt_h up_fmt "$UP"
            TEXT="$up_fmt"
        fi
        ;;
    session-down)
        TEXT="${SESSION_DOWN_FMT:-0}"
        ;;
    session-up)
        TEXT="${SESSION_UP_FMT:-0}"
        ;;
    session-total)
        TEXT="${SESSION_TOTAL_FMT:-0}"
        ;;
    boot-down)
        TEXT="${BOOT_DOWN_FMT:-0}"
        ;;
    boot-up)
        TEXT="${BOOT_UP_FMT:-0}"
        ;;
    boot-total)
        TEXT="${BOOT_TOTAL_FMT:-0}"
        ;;
    *)
        fmt_h up_fmt "$UP"
        fmt_h unit_fmt "$UNIT"
        fmt_h down_fmt "$DOWN"
        TEXT="${up_fmt} ${unit_fmt} ${down_fmt}"
        ;;
esac
OUT=$(printf '{"text":"%s","class":"%s","tooltip":"%s"}\n' "$TEXT" "$CLASS" "$TT")
[[ -d "$CACHE_DIR" ]] || command mkdir -p "$CACHE_DIR" 2>/dev/null
CACHE_TMP="$CACHE_FILE.$BASHPID.tmp"
if ! { printf '%s\n' "$OUT" > "$CACHE_TMP" && mv -fT -- "$CACHE_TMP" "$CACHE_FILE"; } 2>/dev/null; then
    rm -f -- "$CACHE_TMP" 2>/dev/null || true
fi
printf '%s\n' "$OUT"
