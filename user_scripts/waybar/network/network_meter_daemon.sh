#!/usr/bin/env bash
# waybar-netd: Zero-fork optimized network speed daemon
set -euo pipefail

export LC_ALL=C # Force C locale to avoid porblems with various user locales and decimal separators.

# Load 'sleep' as builtin (avoids fork every second)
if [[ -f /usr/lib/bash/sleep ]]; then
    enable -f /usr/lib/bash/sleep sleep 2>/dev/null || true
fi

RUNTIME="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
STATE_DIR="$RUNTIME/waybar-net"
STATE_FILE="$STATE_DIR/state"
HEARTBEAT_FILE="$STATE_DIR/heartbeat"
PID_FILE="$STATE_DIR/daemon.pid"

mkdir -p "$STATE_DIR"
touch "$HEARTBEAT_FILE"
echo $$ > "$PID_FILE"

trap 'rm -rf "$STATE_DIR"' EXIT
trap ':' USR1

# Interface check - FIXED: handle failure gracefully
get_primary_iface() {
    ip route get 1.1.1.1 2>/dev/null | \
        awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}' || :
}

# Pure bash timing via nameref (no subshell)
get_time_us() {
    local -n _out=$1
    local s us
    IFS=. read -r s us <<< "${EPOCHREALTIME:-0.0}"
    us="${us}000000"
    _out=$(( s * 1000000 + 10#${us:0:6} ))
}

# Pure bash speed formatting (replaces awk)
format_speed() {
    local -n _unit=$1 _tx=$2 _rx=$3 _class=$4
    local rx_d=$5 tx_d=$6
    local max=$(( rx_d > tx_d ? rx_d : tx_d ))

    if (( max >= 1048576 )); then
        local tx_x10=$(( tx_d * 10 / 1048576 ))
        local rx_x10=$(( rx_d * 10 / 1048576 ))

        if (( tx_x10 < 100 )); then
            _tx="$((tx_x10 / 10)).$((tx_x10 % 10))"
        else
            _tx="$((tx_x10 / 10))"
        fi

        if (( rx_x10 < 100 )); then
            _rx="$((rx_x10 / 10)).$((rx_x10 % 10))"
        else
            _rx="$((rx_x10 / 10))"
        fi

        _unit="MB"
        _class="network-mb"
    else
        _tx=$(( tx_d / 1024 ))
        _rx=$(( rx_d / 1024 ))
        _unit="KB"
        _class="network-kb"
    fi
}

rx_prev=0
tx_prev=0
iface=""
iface_counter=0
hb_counter=2  # FIXED: Will trigger heartbeat check on first iteration (2+1=3)
hb_time=0

while :; do
    now=$(printf '%(%s)T' -1)

    # WATCHDOG: Check heartbeat every 3 iterations
    if (( ++hb_counter >= 3 )); then
        hb_counter=0
        if [[ -f "$HEARTBEAT_FILE" ]]; then
            hb_time=$(stat -c %Y "$HEARTBEAT_FILE" 2>/dev/null) || hb_time=$now
        else
            hb_time=$now  # FIXED: No heartbeat file = assume active (first run)
        fi
    fi

    # Deep sleep if Waybar inactive >10s
    if (( now - hb_time > 10 )); then
        sleep 600 &
        wait $! || true
        hb_counter=10  # Force check on wake
        continue
    fi

    # INTERFACE CHECK: Every 5 iterations (or if empty)
    if (( ++iface_counter >= 5 )) || [[ -z "$iface" ]]; then
        iface_counter=0
        current_iface=$(get_primary_iface)  # Now safe - returns empty on failure
    else
        current_iface="$iface"
    fi

    # DISCONNECTED
    if [[ -z "$current_iface" ]]; then
        printf '%s\n' "- - - network-disconnected" > "$STATE_FILE.tmp"
        mv -f "$STATE_FILE.tmp" "$STATE_FILE"
        rx_prev=0
        tx_prev=0
        iface=""
        sleep 3 || true
        continue
    fi

    # CONNECTED
    get_time_us start_time

    if [[ "$current_iface" != "$iface" ]]; then
        iface="$current_iface"
        rx_prev=0
        tx_prev=0
    fi

    # Read stats - FIXED: handle missing interface gracefully
    if [[ -r "/sys/class/net/$iface/statistics/rx_bytes" ]] && \
       [[ -r "/sys/class/net/$iface/statistics/tx_bytes" ]]; then
        read -r rx_now < "/sys/class/net/$iface/statistics/rx_bytes" || rx_now=0
        read -r tx_now < "/sys/class/net/$iface/statistics/tx_bytes" || tx_now=0
    else
        rx_now=0
        tx_now=0
    fi

    # First sample: store and wait
    if (( rx_prev == 0 && tx_prev == 0 )); then
        rx_prev=$rx_now
        tx_prev=$tx_now
        sleep 1 || true
        continue
    fi

    # Calculate deltas
    rx_delta=$(( rx_now - rx_prev ))
    tx_delta=$(( tx_now - tx_prev ))
    (( rx_delta < 0 )) && rx_delta=0
    (( tx_delta < 0 )) && tx_delta=0
    rx_prev=$rx_now
    tx_prev=$tx_now

    # Format and write (pure bash - no fork)
    format_speed unit tx_fmt rx_fmt class "$rx_delta" "$tx_delta"
    printf '%s %s %s %s\n' "$unit" "$tx_fmt" "$rx_fmt" "$class" > "$STATE_FILE.tmp"
    mv -f "$STATE_FILE.tmp" "$STATE_FILE"

    # PRECISION SLEEP
    get_time_us end_time
    sleep_us=$(( 1000000 - (end_time - start_time) ))

    if (( sleep_us <= 0 )); then
        :  # Behind schedule, skip sleep
    elif (( sleep_us >= 1000000 )); then
        sleep 1 || true
    else
        printf -v sleep_sec "0.%06d" "$sleep_us"
        sleep "$sleep_sec" || true
    fi
done
