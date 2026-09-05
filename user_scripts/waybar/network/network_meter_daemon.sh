#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC2154
export LC_ALL=C
for _b in sleep stat rm; do
    if [[ -f "/usr/lib/bash/$_b" ]]; then
        enable -f "/usr/lib/bash/$_b" "$_b" 2>/dev/null || true
    fi
done
unset _b
RUNTIME="${XDG_RUNTIME_DIR:-/run/user/${UID:-$(id -u)}}"
STATE_DIR="$RUNTIME/waybar-net"
STATE_FILE="$STATE_DIR/state"
HEARTBEAT_FILE="$STATE_DIR/heartbeat"
PID_FILE="$STATE_DIR/daemon.pid"
: "${STATE_DIR:?empty}"
command mkdir -p "$STATE_DIR"
printf '%s\n' "$$" > "$PID_FILE"
cleanup() {
    local cur=""
    read -r cur < "$PID_FILE" 2>/dev/null || true
    if [[ "$cur" == "$$" ]]; then
        rm -f "$PID_FILE" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap ':' USR1
find_active_iface() {
    local -n _iface_out=$1
    local iface dest
    while read -r iface dest _; do
        if [[ "$dest" == "00000000" ]]; then
            if [[ -r "/sys/class/net/$iface/statistics/rx_bytes" ]]; then
                _iface_out="$iface"
                return 0
            fi
        fi
    done < /proc/net/route
    local path if_name state
    for path in /sys/class/net/*; do
        if_name="${path##*/}"
        [[ "$if_name" == "lo" ]] && continue
        [[ -e "$path/device" ]] || continue
        [[ -r "$path/operstate" ]] || continue
        [[ -r "$path/statistics/rx_bytes" ]] || continue
        read -r state < "$path/operstate" 2>/dev/null || continue
        if [[ "$state" == "up" ]]; then
            _iface_out="$if_name"
            return 0
        fi
    done
    for path in /sys/class/net/*; do
        if_name="${path##*/}"
        [[ "$if_name" == "lo" ]] && continue
        [[ -e "$path/device" ]] || continue
        [[ -r "$path/operstate" ]] || continue
        [[ -r "$path/statistics/rx_bytes" ]] || continue
        read -r state < "$path/operstate" 2>/dev/null || continue
        if [[ "$state" == "unknown" ]]; then
            _iface_out="$if_name"
            return 0
        fi
    done
    _iface_out=""
    return 1
}
format_speed() {
    local -n _unit=$1 _tx=$2 _rx=$3 _class=$4
    local rx_d=$5 tx_d=$6
    local max=$(( rx_d > tx_d ? rx_d : tx_d ))
    if (( max >= 1038090240 )); then
        local tx_x10=$(( (tx_d * 10 + 536870912) / 1073741824 ))
        local rx_x10=$(( (rx_d * 10 + 536870912) / 1073741824 ))
        if (( tx_x10 < 100 )); then _tx="$((tx_x10 / 10)).$((tx_x10 % 10))"; else _tx="$(( (tx_d + 536870912) / 1073741824 ))"; fi
        if (( rx_x10 < 100 )); then _rx="$((rx_x10 / 10)).$((rx_x10 % 10))"; else _rx="$(( (rx_d + 536870912) / 1073741824 ))"; fi
        _unit="GB"
        _class="network-gb"
    elif (( max >= 1013760 )); then
        local tx_x10=$(( (tx_d * 10 + 524288) / 1048576 ))
        local rx_x10=$(( (rx_d * 10 + 524288) / 1048576 ))
        if (( tx_x10 < 100 )); then _tx="$((tx_x10 / 10)).$((tx_x10 % 10))"; else _tx="$(( (tx_d + 524288) / 1048576 ))"; fi
        if (( rx_x10 < 100 )); then _rx="$((rx_x10 / 10)).$((rx_x10 % 10))"; else _rx="$(( (rx_d + 524288) / 1048576 ))"; fi
        _unit="MB"
        _class="network-mb"
    else
        _tx=$(( (tx_d + 512) / 1024 ))
        _rx=$(( (rx_d + 512) / 1024 ))
        _unit="KB"
        _class="network-kb"
    fi
}
check_heartbeat() {
    local -n _hb_time=$1
    local now=$2
    local -A STAT
    if stat -A STAT "$HEARTBEAT_FILE" 2>/dev/null; then
        _hb_time="${STAT[mtime]}"
        return 0
    fi
    local mtime
    if mtime=$(stat -c %Y "$HEARTBEAT_FILE" 2>/dev/null); then
        [[ "$mtime" =~ ^[0-9]+$ ]] && _hb_time="$mtime" || _hb_time="$now"
    else
        _hb_time="$now"
    fi
}
rx_prev=0
tx_prev=0
prev_sample_us=0
initialized=0
iface=""
current_iface=""
iface_counter=0
hb_counter=2
hb_time=0
while :; do
    printf -v now '%(%s)T' -1
    if (( ++hb_counter >= 3 )); then
        hb_counter=0
        check_heartbeat hb_time "$now"
    fi
    if (( now - hb_time > 10 )); then
        initialized=0
        iface=""
        sleep 600 &
        wait $! || true
        hb_counter=10
        continue
    fi
    if (( ++iface_counter >= 5 )) || [[ -z "$iface" ]] || [[ ! -r "/sys/class/net/$iface/statistics/rx_bytes" ]]; then
        iface_counter=0
        find_active_iface current_iface || current_iface=""
    else
        current_iface="$iface"
    fi
    if [[ -z "$current_iface" ]]; then
        printf '%s\n' "- - - network-disconnected" > "$STATE_FILE"
        rx_prev=0; tx_prev=0; prev_sample_us=0; initialized=0; iface=""
        sleep 3 || true
        continue
    fi
    sample_us="${EPOCHREALTIME/./}"
    if [[ "$current_iface" != "$iface" ]]; then
        iface="$current_iface"
        initialized=0
    fi
    read -r rx_now < "/sys/class/net/$iface/statistics/rx_bytes" 2>/dev/null || rx_now=0
    read -r tx_now < "/sys/class/net/$iface/statistics/tx_bytes" 2>/dev/null || tx_now=0
    [[ "$rx_now" =~ ^[0-9]+$ ]] || rx_now=0
    [[ "$tx_now" =~ ^[0-9]+$ ]] || tx_now=0
    if (( initialized == 0 )); then
        rx_prev=$rx_now
        tx_prev=$tx_now
        prev_sample_us=$sample_us
        initialized=1
        sleep 1 || true
        continue
    fi
    dt_us=$(( sample_us - prev_sample_us ))
    if (( dt_us < 400000 || dt_us > 2500000 )); then
        rx_prev=$rx_now
        tx_prev=$tx_now
        prev_sample_us=$sample_us
        sleep 1 || true
        continue
    fi
    rx_delta=$(( rx_now - rx_prev ))
    tx_delta=$(( tx_now - tx_prev ))
    if (( rx_delta < 0 )); then rx_delta=$rx_now; fi
    if (( tx_delta < 0 )); then tx_delta=$tx_now; fi
    rx_prev=$rx_now
    tx_prev=$tx_now
    prev_sample_us=$sample_us
    rx_rate=$(( (rx_delta * 1000000 + dt_us / 2) / dt_us ))
    tx_rate=$(( (tx_delta * 1000000 + dt_us / 2) / dt_us ))
    format_speed unit tx_fmt rx_fmt class "$rx_rate" "$tx_rate"
    # shellcheck disable=SC2154
    printf '%s %s %s %s\n' "$unit" "$tx_fmt" "$rx_fmt" "$class" > "$STATE_FILE"
    end_time="${EPOCHREALTIME/./}"
    sleep_us=$(( 1000000 - (end_time - sample_us) ))
    if (( sleep_us <= 0 )); then
        :
    elif (( sleep_us >= 1000000 )); then
        sleep 1 || true
    else
        printf -v sleep_sec "0.%06d" "$sleep_us"
        sleep "$sleep_sec" || true
    fi
done
