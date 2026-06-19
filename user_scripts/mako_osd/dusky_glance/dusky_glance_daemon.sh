#!/usr/bin/env bash
# ==============================================================================
# DUSKY GLANCE DAEMON - DYNAMIC WIDTH EDITION
# ==============================================================================

set -euo pipefail

SYNC_ID="dusky-glance-sync"
PID_FILE="${XDG_RUNTIME_DIR:-/run/user/$UID}/dusky_glance.pid"

MODE="${1:-}"

# --- DYNAMIC APP NAME RESOLUTION ---
# Separate app names based on mode so mako can style them individually
if [[ -n "$MODE" && "$MODE" != "--stop" ]]; then
    CURRENT_APP="dusky-glance-${MODE#--}"
else
    CURRENT_APP="dusky-glance"
fi

# --- CORE LIFECYCLE ---
clear_osd() {
    notify-send -a "$CURRENT_APP" -h string:x-canonical-private-synchronous:"$SYNC_ID" -t 10 " " " " 2>/dev/null || true
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
    # 1. Kill grandchildren (the pipeline commands like socat inside the subshell)
    for child in $(pgrep -P "$$" 2>/dev/null || true); do
        pkill -P "$child" 2>/dev/null || true
    done
    
    # 2. Kill direct children (the background subshells)
    pkill -P "$$" 2>/dev/null || true

    # 3. Release the lock file
    if [[ -f "$PID_FILE" ]] && [[ "$(<"$PID_FILE")" == "$$" ]]; then
        rm -f "$PID_FILE"
    fi
    
    # 4. Clear the display
    clear_osd
}
trap 'cleanup' EXIT
trap 'exit 0' INT TERM

# --- HELPER ROUTINES ---
send_osd() {
    local text="$1"
    local body="<span font='monospace 20' weight='bold'>${text}</span>"
    notify-send -a "$CURRENT_APP" -h string:x-canonical-private-synchronous:"$SYNC_ID" -t 2000 " " "$body"
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

# --- HARDWARE & STATE MODULES ---
START_SEC=$SECONDS

case "$MODE" in
    --clock)
        while true; do
            printf -v current_time '%(%I:%M:%S)T' -1
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
        
    --timer)
        DURATION_SEC="${2:-900}"
        if (( DURATION_SEC <= 0 )); then exit 1; fi
        TARGET_SEC=$((START_SEC + DURATION_SEC))
        
        while true; do
            left=$((TARGET_SEC - SECONDS))
            if (( left <= 0 )); then
                # Leaving 'dusky-glance-alert' intact as requested via config overrides
                notify-send -u critical -a "dusky-glance-alert" "Time's Up!" "Your timer has finished."
                play_sound "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"
                
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

    --pomodoro)
        WORK_SEC="${2:-1500}"
        BREAK_SEC="${3:-300}"
        
        if (( WORK_SEC <= 0 )); then 
            send_osd "Invalid Time"
            sleep 2
            exit 1
        fi
        
        PHASE="WORK"
        TARGET_SEC=$((START_SEC + WORK_SEC))
        
        while true; do
            left=$((TARGET_SEC - SECONDS))
            
            if (( left <= 0 )); then
                if [[ "$PHASE" == "WORK" ]] && (( BREAK_SEC > 0 )); then
                    notify-send -u critical -a "dusky-glance-alert" "Break Time!" "Time to take a break!"
                    play_sound "/usr/share/sounds/gnome/default/alarms/glass-bell.oga"
                    
                    PHASE="BREAK"
                    TARGET_SEC=$((SECONDS + BREAK_SEC))
                    continue
                else
                    msg="Work session finished."
                    (( BREAK_SEC > 0 )) && msg="Break is over. Back to work!"
                    
                    notify-send -u critical -a "dusky-glance-alert" "Pomodoro Cycle" "$msg"
                    play_sound "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"
                    
                    PHASE="WORK"
                    TARGET_SEC=$((SECONDS + WORK_SEC))
                    continue
                fi
            fi
            
            prefix=""
            [[ "$PHASE" == "BREAK" ]] && prefix="B "
            send_osd "${prefix}$(format_time "$left")"
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
                send_osd "${usage}%"
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
            send_osd "${ram_mb}"
            sleep 1
        done
        ;;

    --ram-temp)
        # Discover all DDR5 SPD5118 temperature sensors
        temp_files=()
        for hwmon_dir in /sys/class/hwmon/hwmon*/; do
            name_file="${hwmon_dir}name"
            [[ -f "$name_file" ]] || continue
            read -r name < "$name_file"
            if [[ "$name" == "spd5118" || "$name" == "jc42" || "$name" == "tmp421" ]]; then
                tfile="${hwmon_dir}temp1_input"
                [[ -f "$tfile" ]] && temp_files+=("$tfile")
            fi
        done

        while true; do
            if [[ ${#temp_files[@]} -gt 0 ]]; then
                temps=()
                for tf in "${temp_files[@]}"; do
                    if read -r t < "$tf" 2>/dev/null; then
                        temps+=("$((t/1000))°")
                    fi
                done
                if [[ ${#temps[@]} -gt 0 ]]; then
                    send_osd "${temps[*]}"
                else
                    send_osd "N/A"
                fi
            else
                send_osd "N/A"
            fi
            sleep 1
        done
        ;;

    --zram)
        zram_file="/sys/block/zram0/mm_stat"
        while true; do
            if [[ -f "$zram_file" ]] && read -r orig_data compr_data mem_used _ _ _ _ _ _ < "$zram_file" 2>/dev/null; then
                used_mb=$(( mem_used / 1048576 ))
                if (( compr_data > 0 )); then
                    ratio=$(( orig_data / compr_data ))
                    send_osd "${used_mb}MB ${ratio}:1"
                else
                    send_osd "${used_mb}MB"
                fi
            else
                send_osd "N/A"
            fi
            sleep 1
        done
        ;;

    --temp)
        zone_file=""
        
        for hwmon in /sys/class/hwmon/hwmon*/name; do
            [[ -r "$hwmon" ]] || continue
            read -r name < "$hwmon"
            if [[ "$name" == "coretemp" || "$name" == "k10temp" || "$name" == "zenpower" || "$name" == "cpu_thermal" ]]; then
                dir="${hwmon%/*}"
                if [[ -r "$dir/temp1_input" ]]; then
                    zone_file="$dir/temp1_input"
                    break
                fi
            fi
        done
        
        if [[ -z "$zone_file" ]]; then
            for tz in /sys/class/thermal/thermal_zone*/type; do
                [[ -r "$tz" ]] || continue
                read -r type < "$tz"
                if [[ "$type" == *"x86_pkg_temp"* || "$type" == *"cpu"* ]]; then
                    dir="${tz%/*}"
                    if [[ -r "$dir/temp" ]]; then
                        zone_file="$dir/temp"
                        break
                    fi
                fi
            done
        fi
        
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

    --disk)
        while true; do
            {
                read -r _ # Discard the header row
                read -r used size pcent
            } < <(df -h --output=used,size,pcent /)

            send_osd "${used}/${size} ${pcent}"
            sleep 1
        done
        ;;

    --disk-read)
        DEV="${2:-}"
        stat_file="/sys/block/$DEV/stat"
        if [[ -z "$DEV" || ! -f "$stat_file" ]]; then
            send_osd "Unknown Drive"
            exit 1
        fi

        prev_read_sec=0
        if read -r -a stats < "$stat_file"; then
            prev_read_sec=${stats[2]}
        fi

        while true; do
            if read -r -a stats < "$stat_file"; then
                curr_read_sec=${stats[2]}
                
                # OPTIMIZATION: Native Bash arithmetic replaces awk. Zero subprocess overhead, exact integer mapping.
                read_mb_s=$(( (curr_read_sec - prev_read_sec) * 512 / 1048576 ))
                tot_read_mb=$(( curr_read_sec * 512 / 1048576 ))

                send_osd "${tot_read_mb} ${read_mb_s}"
                
                prev_read_sec=$curr_read_sec
            fi
            sleep 1
        done
        ;;

    --disk-write)
        DEV="${2:-}"
        stat_file="/sys/block/$DEV/stat"
        if [[ -z "$DEV" || ! -f "$stat_file" ]]; then
            send_osd "Unknown Drive"
            exit 1
        fi

        prev_write_sec=0
        if read -r -a stats < "$stat_file"; then
            prev_write_sec=${stats[6]}
        fi

        while true; do
            if read -r -a stats < "$stat_file"; then
                curr_write_sec=${stats[6]}
                
                # OPTIMIZATION: Native Bash arithmetic replaces awk. Zero subprocess overhead, exact integer mapping.
                write_mb_s=$(( (curr_write_sec - prev_write_sec) * 512 / 1048576 ))
                tot_write_mb=$(( curr_write_sec * 512 / 1048576 ))

                send_osd "${tot_write_mb} ${write_mb_s}"
                
                prev_write_sec=$curr_write_sec
            fi
            sleep 1
        done
        ;;

    --disk-temp)
        DEV="${2:-}"
        stat_file="/sys/block/$DEV/stat"
        if [[ -z "$DEV" || ! -f "$stat_file" ]]; then
            send_osd "Unknown Drive"
            exit 1
        fi

        # Method 1: Kernel hwmon (fastest, zero subprocess)
        temp_files=()
        for tfile in /sys/block/"$DEV"/device/hwmon*/temp*_input; do
            [[ -f "$tfile" ]] && temp_files+=("$tfile")
        done
        if [[ ${#temp_files[@]} -eq 0 && "$DEV" == nvme* ]]; then
            ctrl_dev=$(echo "$DEV" | grep -o 'nvme[0-9]\+')
            for tfile in /sys/class/nvme/"$ctrl_dev"/hwmon*/temp*_input; do
                [[ -f "$tfile" ]] && temp_files+=("$tfile")
            done
        fi

        # Method 2: smartctl (USB enclosures, external drives without hwmon)
        use_smartctl=false
        if [[ ${#temp_files[@]} -eq 0 ]] && command -v smartctl >/dev/null 2>&1; then
            use_smartctl=true
        fi

        while true; do
            if [[ ${#temp_files[@]} -gt 0 ]]; then
                temps=()
                for tf in "${temp_files[@]}"; do
                    if read -r t < "$tf" 2>/dev/null; then
                        temps+=("$((t/1000))°")
                    fi
                done
                if [[ ${#temps[@]} -gt 0 ]]; then
                    send_osd "${temps[*]}"
                else
                    send_osd "N/A"
                fi
            elif [[ "$use_smartctl" == true ]]; then
                smart_out=$(smartctl -A "/dev/$DEV" 2>/dev/null || sudo smartctl -A "/dev/$DEV" 2>/dev/null)
                if [[ -n "$smart_out" ]]; then
                    # SATA: "194 Temperature_Celsius ... 32" → last field
                    # NVMe: "Temperature: 34 Celsius" → field after colon
                    temps=()
                    while IFS= read -r val; do
                        [[ "$val" =~ ^[0-9]+$ ]] && (( val > 0 && val < 200 )) && temps+=("${val}°")
                    done < <(echo "$smart_out" | awk '/Temperature_Celsius/{print $NF} /^Temperature:/{print $2}')
                    if [[ ${#temps[@]} -gt 0 ]]; then
                        send_osd "${temps[*]}"
                    else
                        send_osd "N/A"
                    fi
                else
                    send_osd "N/A"
                fi
            else
                send_osd "N/A"
            fi
            sleep 1
        done
        ;;

    --network)
        STATE_DIR="${XDG_RUNTIME_DIR:-/run/user/$UID}/waybar-net"
        STATE_FILE="$STATE_DIR/state"
        HEARTBEAT_FILE="$STATE_DIR/heartbeat"
        DAEMON_PID_FILE="$STATE_DIR/daemon.pid"
        
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
        
    --uptime)
        while true; do
            if read -r up_time _ < /proc/uptime; then
                up_sec=${up_time%%.*}
                h=$(( up_sec / 3600 ))
                m=$(( (up_sec % 3600) / 60 ))
                s=$(( up_sec % 60 ))
                printf -v fmt_up "%02d:%02d:%02d" "$h" "$m" "$s"
                send_osd "$fmt_up"
            else
                send_osd "Up: N/A"
            fi
            sleep 1
        done
        ;;
        
    --workspace)
        if [[ -z "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]]; then
            send_osd "WS: ?"
            exit 1
        fi
        
        if ws_info=$(hyprctl activeworkspace 2>/dev/null); then
            ws_id=$(awk '/workspace ID/ {print $3}' <<< "$ws_info")
            send_osd "WS: ${ws_id:-?}"
        else
            send_osd "WS: ?"
        fi

        socket_path="$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket2.sock"
        if command -v socat >/dev/null 2>&1 && [[ -S "$socket_path" ]]; then
            # OPTIMIZATION: Background the pipeline directly without the { } wrapper
            socat -U - UNIX-CONNECT:"$socket_path" 2>/dev/null | while read -r line; do
                if [[ "$line" == "workspace>>"* ]]; then
                    send_osd "WS: ${line#workspace>>}"
                fi
            done &
            
            bg_pid=$!
            
            wait "$bg_pid" 2>/dev/null || true
        else
            while true; do
                if ws_info=$(hyprctl activeworkspace 2>/dev/null); then
                    ws_id=$(awk '/workspace ID/ {print $3}' <<< "$ws_info")
                    send_osd "WS: ${ws_id:-?}"
                fi
                sleep 1
            done
        fi
        ;;
esac
