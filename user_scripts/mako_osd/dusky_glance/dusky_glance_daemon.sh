#!/usr/bin/env bash
# ==============================================================================
# DUSKY GLANCE DAEMON - ULTRA-MINIMALIST HARDWARE MONITORING 
# ==============================================================================

set -euo pipefail

APP_NAME="dusky-glance"
SYNC_ID="${APP_NAME}-sync"
PID_FILE="${XDG_RUNTIME_DIR:-/run/user/$UID}/dusky_glance.pid"

MODE="${1:-}"

# --- CORE LIFECYCLE ---
clear_osd() {
    notify-send -a "$APP_NAME" -h string:x-canonical-private-synchronous:"$SYNC_ID" -t 10 " " " " 2>/dev/null || true
}

if [[ -f "$PID_FILE" ]]; then
    old_pid=$(<"$PID_FILE")
    if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null && [[ "$old_pid" != "$$" ]]; then
        kill -15 "$old_pid" 2>/dev/null || true
        for ((i=0; i<20; i++)); do
            kill -0 "$old_pid" 2>/dev/null || break
            sleep 0.05
        done
    fi
fi

if [[ "$MODE" == "--stop" ]]; then
    clear_osd
    exit 0
fi

echo "$$" > "$PID_FILE"

cleanup() {
    if [[ -f "$PID_FILE" ]] && [[ "$(<"$PID_FILE")" == "$$" ]]; then
        rm -f "$PID_FILE"
    fi
    clear_osd
}
trap 'cleanup' EXIT
trap 'exit 0' INT TERM

# --- HELPER ROUTINES ---
send_osd() {
    local text="$1"
    local body="<span font='monospace 20' weight='bold'>${text}</span>"
    notify-send -a "$APP_NAME" -h string:x-canonical-private-synchronous:"$SYNC_ID" -t 2000 " " "$body"
}

format_time() {
    local total_sec=$1
    local h=$((total_sec / 3600))
    local m=$(( (total_sec % 3600) / 60 ))
    local s=$((total_sec % 60))
    if (( h > 0 )); then
        printf "%02d:%02d:%02d\n" "$h" "$m" "$s"
    else
        printf "%02d:%02d\n" "$m" "$s"
    fi
}

play_sound() {
    local snd="$1"
    if command -v pw-play >/dev/null 2>&1; then
        { pw-play "$snd" >/dev/null 2>&1 & disown; } || true
    elif command -v paplay >/dev/null 2>&1; then
        { paplay "$snd" >/dev/null 2>&1 & disown; } || true
    fi
}

# --- HARDWARE MODULES (STRICTLY < 10 CHARACTERS) ---
START_SEC=$SECONDS

case "$MODE" in
    --clock)
        while true; do
            printf -v current_time '%(%H:%M:%S)T' -1
            send_osd "$current_time"
            sleep 1
        done
        ;;
        
    --stopwatch)
        while true; do
            elapsed=$((SECONDS - START_SEC))
            send_osd "$(format_time "$elapsed")"
            sleep 1
        done
        ;;
        
    --timer|--pomodoro)
        DURATION_SEC="${2:-0}"
        if (( DURATION_SEC <= 0 )); then exit 1; fi
        TARGET_SEC=$((START_SEC + DURATION_SEC))
        
        while true; do
            left=$((TARGET_SEC - SECONDS))
            if (( left <= 0 )); then
                if [[ "$MODE" == "--pomodoro" ]]; then
                    play_sound "/usr/share/sounds/gnome/default/alarms/glass-bell.oga"
                else
                    play_sound "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"
                fi
                for _ in {1..5}; do
                    send_osd "00:00"
                    sleep 0.5
                    send_osd "     "
                    sleep 0.5
                done
                exit 0
            fi
            send_osd "$(format_time "$left")"
            sleep 1
        done
        ;;

    --cpu)
        prev_idle=0; prev_total=0
        while true; do
            read -r _ user nice system idle iowait irq softirq steal _ < /proc/stat
            total=$((user + nice + system + idle + iowait + irq + softirq + steal))
            diff_idle=$((idle - prev_idle))
            diff_total=$((total - prev_total))
            
            if (( prev_total > 0 && diff_total > 0 )); then
                usage=$(( 100 * (diff_total - diff_idle) / diff_total ))
                send_osd "CPU ${usage}%"
            fi
            
            prev_idle=$idle
            prev_total=$total
            sleep 1
        done
        ;;

    --ram)
        while true; do
            mem_tot=0; mem_avail=0
            while read -r key val _; do
                case "$key" in
                    MemTotal:) mem_tot=$val ;;
                    MemAvailable:) mem_avail=$val ;;
                esac
            done < /proc/meminfo
            
            ram_mb=$(( (mem_tot - mem_avail) / 1024 ))
            send_osd "${ram_mb} MB"
            sleep 1
        done
        ;;

    --temp)
        zone_file=""
        for z in /sys/class/hwmon/hwmon*/temp1_input /sys/class/thermal/thermal_zone*/temp; do
            if [[ -r "$z" ]]; then
                zone_file="$z"
                break
            fi
        done
        
        while true; do
            if [[ -n "$zone_file" ]] && read -r t < "$zone_file" 2>/dev/null; then
                temp_c=$(( t / 1000 ))
                send_osd "${temp_c}°C"
            else
                send_osd "N/A"
            fi
            sleep 1
        done
        ;;

    --battery)
        bat_dir=""
        for d in /sys/class/power_supply/*; do
            if [[ -f "$d/type" ]]; then
                read -r type < "$d/type" 2>/dev/null || continue
                if [[ "$type" == "Battery" ]]; then
                    bat_dir="$d"
                    break
                fi
            fi
        done

        while true; do
            if [[ -n "$bat_dir" ]]; then
                read -r cap < "$bat_dir/capacity" 2>/dev/null || cap="?"
                
                watts_int=0; watts_frac=0
                if [[ -f "$bat_dir/power_now" ]]; then
                    read -r pwr < "$bat_dir/power_now" 2>/dev/null || pwr=0
                    watts_int=$(( pwr / 1000000 ))
                    watts_frac=$(( (pwr % 1000000) / 100000 ))
                elif [[ -f "$bat_dir/current_now" && -f "$bat_dir/voltage_now" ]]; then
                    read -r curr < "$bat_dir/current_now" 2>/dev/null || curr=0
                    read -r volt < "$bat_dir/voltage_now" 2>/dev/null || volt=0
                    p_uw=$(( (curr / 1000) * (volt / 1000) ))
                    watts_int=$(( p_uw / 1000000 ))
                    watts_frac=$(( (p_uw % 1000000) / 100000 ))
                fi
                
                printf -v out_str "%s%% %d.%dW" "$cap" "$watts_int" "$watts_frac"
                send_osd "$out_str"
            else
                send_osd "Bat: N/A"
            fi
            sleep 1
        done
        ;;

    --network)
        STATE_DIR="${XDG_RUNTIME_DIR:-/run/user/$UID}/waybar-net"
        STATE_FILE="$STATE_DIR/state"
        HEARTBEAT_FILE="$STATE_DIR/heartbeat"
        DAEMON_PID_FILE="$STATE_DIR/daemon.pid"
        
        # --- ONE-TIME WAKEUP PING ---
        if [[ -d "$STATE_DIR" ]]; then
            printf "" > "$HEARTBEAT_FILE"
            if [[ -r "$DAEMON_PID_FILE" ]]; then
                read -r d_pid < "$DAEMON_PID_FILE" 2>/dev/null || d_pid=""
                if [[ -n "$d_pid" ]] && kill -0 "$d_pid" 2>/dev/null; then
                    kill -USR1 "$d_pid" 2>/dev/null || true
                fi
            fi
        fi
        
        while true; do
            # KEEP AWAKE
            [[ -d "$STATE_DIR" ]] && printf "" > "$HEARTBEAT_FILE"
            
            if [[ -r "$STATE_FILE" ]]; then
                read -r unit up down _ < "$STATE_FILE" || true
                up="${up:-0}"; down="${down:-0}"; unit="${unit:-B}"
                
                short_unit="${unit%B}"
                
                send_osd "${up}${short_unit} ${down}${short_unit}"
            else
                send_osd "Offline"
            fi
            sleep 1
        done
        ;;
esac
