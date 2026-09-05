#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC2154
export LC_ALL=C

# 1. Load builtins if available (critical for loop performance & zero-fork execution)
for _b in sleep stat rm; do
    if [[ -f "/usr/lib/bash/$_b" ]]; then
        enable -f "/usr/lib/bash/$_b" "$_b" 2>/dev/null || true
    fi
done

# 2. Environment Setup
RUNTIME="${XDG_RUNTIME_DIR:-/run/user/${UID:-$(id -u)}}"
STATE_DIR="$RUNTIME/waybar-net"
STATE_FILE="$STATE_DIR/state"
HEARTBEAT_FILE="$STATE_DIR/heartbeat"
PID_FILE="$STATE_DIR/daemon.pid"

mkdir -p "$STATE_DIR"
printf '%s\n' "$$" > "$PID_FILE"

# Cleanup on exit
trap 'rm -rf "$STATE_DIR"' EXIT
trap ':' USR1

# 3. HELPER: Interface Detection (ZERO FORK)
# Strategy: Read /proc/net/route directly.
# If the default route interface lacks stats (VPN bug), scan for physical fallback.
find_active_iface() {
    local -n _iface_out=$1
    local iface dest
    
    # Attempt 1: Check Default Route (Destination 00000000)
    while read -r iface dest _; do
        if [[ "$dest" == "00000000" ]]; then
            # Verify this interface actually exposes statistics
            if [[ -r "/sys/class/net/$iface/statistics/rx_bytes" ]]; then
                _iface_out="$iface"
                return 0
            fi
        fi
    done < /proc/net/route

    # Attempt 2: Fallback - Find first UP physical interface with stats
    # (Fixes issues where VPN tunnels mask the real interface but offer no stats)
    for path in /sys/class/net/*; do
        local if_name="${path##*/}"
        [[ "$if_name" == "lo" ]] && continue
        [[ -r "$path/operstate" ]] || continue
        [[ -r "$path/statistics/rx_bytes" ]] || continue
        
        local state
        read -r state < "$path/operstate" || state="unknown"
        if [[ "$state" == "up" ]]; then
            _iface_out="$if_name"
            return 0
        fi
    done

    _iface_out=""
    return 1
}

# 4. HELPER: Format Speed (Pure Bash Math with Precision Rounding & GB/s Support)
format_speed() {
    local -n _unit=$1 _tx=$2 _rx=$3 _class=$4
    local rx_d=$5 tx_d=$6
    local max=$(( rx_d > tx_d ? rx_d : tx_d ))

    if (( max >= 1073741824 )); then
        # GB/s: 1 GB = 1073741824 bytes
        local tx_x10=$(( (tx_d * 10 + 536870912) / 1073741824 ))
        local rx_x10=$(( (rx_d * 10 + 536870912) / 1073741824 ))

        (( tx_x10 < 100 )) && _tx="$((tx_x10 / 10)).$((tx_x10 % 10))" || _tx="$(( (tx_d + 536870912) / 1073741824 ))"
        (( rx_x10 < 100 )) && _rx="$((rx_x10 / 10)).$((rx_x10 % 10))" || _rx="$(( (rx_d + 536870912) / 1073741824 ))"

        _unit="GB"
        _class="network-gb"
    elif (( max >= 1048576 )); then
        # MB/s: 1 MB = 1048576 bytes
        local tx_x10=$(( (tx_d * 10 + 524288) / 1048576 ))
        local rx_x10=$(( (rx_d * 10 + 524288) / 1048576 ))

        (( tx_x10 < 100 )) && _tx="$((tx_x10 / 10)).$((tx_x10 % 10))" || _tx="$(( (tx_d + 524288) / 1048576 ))"
        (( rx_x10 < 100 )) && _rx="$((rx_x10 / 10)).$((rx_x10 % 10))" || _rx="$(( (rx_d + 524288) / 1048576 ))"

        _unit="MB"
        _class="network-mb"
    else
        # KB/s: 1 KB = 1024 bytes
        _tx=$(( (tx_d + 512) / 1024 ))
        _rx=$(( (rx_d + 512) / 1024 ))
        _unit="KB"
        _class="network-kb"
    fi
}

# 5. HELPER: Heartbeat Check (Optimization - Forkless stat builtin)
check_heartbeat() {
    local -n _hb_time=$1
    local now=$2
    
    local -A STAT
    if stat -A STAT "$HEARTBEAT_FILE" 2>/dev/null; then
        _hb_time="${STAT[mtime]}"
    else
        _hb_time=$now
    fi
}

# --- MAIN LOOP ---

rx_prev=0
tx_prev=0
iface=""
current_iface=""
iface_counter=0
hb_counter=2
hb_time=0

while :; do
    printf -v now '%(%s)T' -1

    # A. Watchdog (Heartbeat) - Runs every 3 ticks
    if (( ++hb_counter >= 3 )); then
        hb_counter=0
        check_heartbeat hb_time "$now"
    fi

    # B. Deep Sleep if Waybar is inactive (>10s silence)
    if (( now - hb_time > 10 )); then
        sleep 600 &
        wait $! || true
        hb_counter=10 # Force immediate check on wake
        continue
    fi

    # C. Interface Check - Runs every 5 ticks OR if we lost the interface
    # Re-validates stats existence to catch VPN toggles on the fly
    if (( ++iface_counter >= 5 )) || [[ -z "$iface" ]] || [[ ! -r "/sys/class/net/$iface/statistics/rx_bytes" ]]; then
        iface_counter=0
        find_active_iface current_iface || current_iface=""
    else
        current_iface="$iface"
    fi

    # D. Disconnected State
    if [[ -z "$current_iface" ]]; then
        printf '%s\n' "- - - network-disconnected" > "$STATE_FILE"
        rx_prev=0; tx_prev=0; iface=""
        sleep 3 || true
        continue
    fi

    # E. Connected State - Measure
    start_time="${EPOCHREALTIME/./}"

    # Reset counters if interface changed
    if [[ "$current_iface" != "$iface" ]]; then
        iface="$current_iface"
        rx_prev=0; tx_prev=0
    fi

    # Read Stats (No Forks)
    # Using 'read' directly from /sys is incredibly fast
    read -r rx_now < "/sys/class/net/$iface/statistics/rx_bytes" 2>/dev/null || rx_now=0
    read -r tx_now < "/sys/class/net/$iface/statistics/tx_bytes" 2>/dev/null || tx_now=0

    # First sample initialization
    if (( rx_prev == 0 && tx_prev == 0 )); then
        rx_prev=$rx_now
        tx_prev=$tx_now
        sleep 1 || true
        continue
    fi

    # F. Calculate Deltas with Overflow Protection
    rx_delta=$(( rx_now - rx_prev ))
    tx_delta=$(( tx_now - tx_prev ))

    # Handle counter wrap-around or interface resets
    (( rx_delta < 0 )) && rx_delta=$rx_now
    (( tx_delta < 0 )) && tx_delta=$tx_now

    rx_prev=$rx_now
    tx_prev=$tx_now

    # G. Format and Write (Zero-Fork tmpfs write)
    format_speed unit tx_fmt rx_fmt class "$rx_delta" "$tx_delta"
    # shellcheck disable=SC2154
    printf '%s %s %s %s\n' "$unit" "$tx_fmt" "$rx_fmt" "$class" > "$STATE_FILE"

    # H. Precision Sleep
    end_time="${EPOCHREALTIME/./}"
    sleep_us=$(( 1000000 - (end_time - start_time) ))

    if (( sleep_us <= 0 )); then
        : # Lagging behind, run immediately
    elif (( sleep_us >= 1000000 )); then
        sleep 1 || true
    else
        printf -v sleep_sec "0.%06d" "$sleep_us"
        sleep "$sleep_sec" || true
    fi
done
