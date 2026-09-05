#!/usr/bin/env bash
# ==============================================================================
# DUSKY GLANCE DAEMON - DYNAMIC WIDTH EDITION
# ==============================================================================

set -euo pipefail

RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$UID}"
MODE="${1:-}"

if [[ -z "$MODE" ]]; then
    echo "Usage: $0 --mode [args]"
    exit 1
fi

# Generate a clean mode slug for per-mode PID files and D-Bus sync channels
get_mode_slug() {
    local str="$*"
    str="${str#--}"
    echo "$str" | tr '/ ' '--'
}

MODE_SLUG=$(get_mode_slug "$@")
MODE_BASE="${MODE#--}"

# APP matches mako.ini rules (e.g. dusky-glance-cpu, dusky-glance-ram, dusky-glance-hud)
CURRENT_APP="dusky-glance-${MODE_BASE%% *}"
SYNC_ID="dusky-glance-sync-${MODE_SLUG}"
PID_FILE="${RUNTIME_DIR}/dusky_glance_${MODE_SLUG}.pid"

# --- CORE LIFECYCLE ---
clear_osd() {
    local app="${1:-$CURRENT_APP}"
    local sync="${2:-$SYNC_ID}"
    if command -v makoctl >/dev/null 2>&1; then
        local ids
        ids=$(makoctl list -j 2>/dev/null | awk -v target="$app" '
            /"id":/ { id=$2; gsub(/[^0-9]/, "", id) }
            /"app_name":/ {
                a=$2; gsub(/["\t,]/, "", a)
                if (a == target) print id
            }
        ')
        for id in $ids; do
            makoctl dismiss -n "$id" -h 2>/dev/null || true
        done
    fi
    notify-send -a "$app" -h string:x-canonical-private-synchronous:"$sync" -t 10 " " " " 2>/dev/null || true
}

stop_all_daemons() {
    for pf in "${RUNTIME_DIR}"/dusky_glance_*.pid; do
        [[ -f "$pf" ]] || continue
        local old_pid
        old_pid=$(<"$pf")
        if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
            kill -15 "$old_pid" 2>/dev/null || true
            pkill -P "$old_pid" 2>/dev/null || true
        fi
        rm -f "$pf"
    done
    if command -v makoctl >/dev/null 2>&1; then
        local ids
        ids=$(makoctl list -j 2>/dev/null | awk '
            /"id":/ { id=$2; gsub(/[^0-9]/, "", id) }
            /"app_name":/ {
                a=$2; gsub(/["\t,]/, "", a)
                if (a ~ /^dusky-glance/) print id
            }
        ')
        for id in $ids; do
            makoctl dismiss -n "$id" -h 2>/dev/null || true
        done
    fi
}

# Global Stop
if [[ "$MODE" == "--stop" || "$MODE" == "--stop-all" ]]; then
    stop_all_daemons
    exit 0
fi

IS_DAEMON_OWNER=false
MY_PID=$BASHPID

# Atomic Lock Creation: set -C (noclobber) fails atomically if file exists (no TOCTOU race)
if ! ( set -C; echo "$MY_PID" > "$PID_FILE" ) 2>/dev/null; then
    old_pid=$(tr -d '[:space:]' < "$PID_FILE" 2>/dev/null || echo "")
    if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null && [[ "$old_pid" != "$MY_PID" ]]; then
        rm -f "$PID_FILE" 2>/dev/null || true
        kill -9 "$old_pid" 2>/dev/null || true
        pkill -9 -P "$old_pid" 2>/dev/null || true
        clear_osd
        exit 0
    else
        # Stale lockfile recovery
        rm -f "$PID_FILE" 2>/dev/null || true
        echo "$MY_PID" > "$PID_FILE"
        IS_DAEMON_OWNER=true
    fi
else
    IS_DAEMON_OWNER=true
fi

cleanup() {
    if [[ "$IS_DAEMON_OWNER" == true ]]; then
        local pf_content=""
        [[ -f "$PID_FILE" ]] && pf_content=$(cat "$PID_FILE" 2>/dev/null || echo "")
        if [[ "$pf_content" == "$MY_PID" ]]; then
            rm -f "$PID_FILE" 2>/dev/null || true
        fi
        for child in $(pgrep -P "$MY_PID" 2>/dev/null || true); do
            kill -9 "$child" 2>/dev/null || true
        done
        clear_osd
    fi
}
trap 'cleanup' EXIT
trap 'exit 0' INT TERM

# --- HELPER ROUTINES ---
send_osd() {
    [[ -f "$PID_FILE" ]] || exit 0
    local text="$1"
    local body="<span font='monospace 20' weight='bold'>${text}</span>"
    notify-send -a "$CURRENT_APP" -h string:x-canonical-private-synchronous:"$SYNC_ID" -t 2000 " " "$body" 2>/dev/null || true
}

send_hud_osd() {
    [[ -f "$PID_FILE" ]] || exit 0
    local text="$1"
    local body="<span font='monospace 9' weight='bold'>${text}</span>"
    notify-send -a "$CURRENT_APP" -h string:x-canonical-private-synchronous:"$SYNC_ID" -t 0 " " "$body" 2>/dev/null || true
}

format_time() {
    local -n _out_ref=$1
    local total_sec=$2
    local h=$((total_sec / 3600))
    local m=$(( (total_sec % 3600) / 60 ))
    local s=$((total_sec % 60))
    if (( h > 0 )); then
        printf -v _out_ref "%02d:%02d:%02d" "$h" "$m" "$s"
    else
        printf -v _out_ref "%02d:%02d" "$m" "$s"
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

offset_to_minutes() {
    local offset="$1"
    local sign="${offset:0:1}"
    local hours="${offset:1:2}"
    local mins="${offset:3:2}"
    hours=$((10#$hours))
    mins=$((10#$mins))
    local total_mins=$((hours * 60 + mins))
    if [[ "$sign" == "-" ]]; then
        total_mins=$((-total_mins))
    fi
    echo "$total_mins"
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

    --clock-short)
        while true; do
            printf -v current_time '%(%I:%M)T' -1
            send_osd "$current_time"
            sleep 1
        done
        ;;

    --world-clock)
        tz_name="${2:-}"
        place_lbl="${3:-Time}"
        if [[ -z "$tz_name" ]]; then
            send_osd "N/A"
            exit 1
        fi
        
        while true; do
            time_str=$(TZ="$tz_name" date +"%I:%M:%S %p")
            local_offset=$(date +%z)
            target_offset=$(TZ="$tz_name" date +%z)
            
            local_min=$(offset_to_minutes "$local_offset")
            target_min=$(offset_to_minutes "$target_offset")
            diff_min=$((target_min - local_min))
            
            if (( diff_min == 0 )); then
                diff_str="same"
            else
                diff_hours=$((diff_min / 60))
                diff_mins=$((diff_min % 60))
                abs_hours=${diff_hours#-}
                abs_mins=${diff_mins#-}
                
                sign=""
                if (( diff_min > 0 )); then
                    sign="+"
                else
                    sign="-"
                fi
                
                if (( abs_mins == 0 )); then
                    diff_str="${sign}${abs_hours}h"
                else
                    diff_str="${sign}${abs_hours}h ${abs_mins}m"
                fi
            fi
            
            diff_lbl="$diff_str"
            [[ "$diff_lbl" == "same" ]] && diff_lbl="same time"
            
            body="<span font='monospace 11' weight='bold'>${time_str}</span>\n<span font='monospace 9'>${diff_lbl}</span>"
            notify-send -a "$CURRENT_APP" -h string:x-canonical-private-synchronous:"$SYNC_ID" -t 2000 " " "$body" 2>/dev/null || true
            sleep 1
        done
        ;;
        
    --stopwatch)
        while true; do
            elapsed=$((SECONDS - START_SEC))
            format_time time_str "$elapsed"
            send_osd "$time_str"
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
                notify-send -u critical -a "dusky-glance-alert" -h string:x-canonical-private-synchronous:dusky-timer-alert "󰔛  Time's Up!" 2>/dev/null || true
                play_sound "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"
                
                for _ in {1..5}; do
                    send_osd "00:00"
                    sleep 0.5
                    send_osd "     "
                    sleep 0.5
                done
                exit 0
            fi
            format_time time_str "$left"
            send_osd "$time_str"
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
                    notify-send -u critical -a "dusky-glance-alert" -h string:x-canonical-private-synchronous:dusky-timer-alert "󰦖  Break Time!" 2>/dev/null || true
                    play_sound "/usr/share/sounds/gnome/default/alarms/glass-bell.oga"
                    
                    PHASE="BREAK"
                    TARGET_SEC=$((SECONDS + BREAK_SEC))
                    continue
                else
                    msg="Session Finished"
                    (( BREAK_SEC > 0 )) && msg="Back to Work!"
                    
                    notify-send -u critical -a "dusky-glance-alert" -h string:x-canonical-private-synchronous:dusky-timer-alert "󰔚  $msg" 2>/dev/null || true
                    play_sound "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"
                    
                    PHASE="WORK"
                    TARGET_SEC=$((SECONDS + WORK_SEC))
                    continue
                fi
            fi
            
            prefix=""
            [[ "$PHASE" == "BREAK" ]] && prefix="B "
            format_time time_str "$left"
            send_osd "${prefix}${time_str}"
            sleep 1
        done
        ;;

    --cpu-power)
        path="/sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj"
        if [[ ! -r "$path" ]]; then
            send_osd "N/A"
            exit 1
        fi
        
        # Read initial values
        read -r last_energy < "$path"
        last_time_str="${EPOCHREALTIME//./}"
        
        sleep 1
        
        while true; do
            if read -r current_energy < "$path" 2>/dev/null; then
                curr_time_str="${EPOCHREALTIME//./}"
                
                delta_energy=$((current_energy - last_energy))
                delta_time_us=$((curr_time_str - last_time_str))
                
                if (( delta_time_us > 0 )); then
                    if (( delta_energy < 0 )); then
                        # Dynamic counter rollover compensation via kernel max_energy_range_uj
                        max_range=4294967296
                        read -r max_range < "${path%/*}/max_energy_range_uj" 2>/dev/null || true
                        delta_energy=$(( delta_energy + max_range ))
                    fi
                    watts_x10=$(( (delta_energy * 10) / delta_time_us ))
                    watts_int=$(( watts_x10 / 10 ))
                    watts_frac=$(( watts_x10 % 10 ))
                    send_osd "${watts_int}.${watts_frac}W"
                fi
                
                last_energy=$current_energy
                last_time_str=$curr_time_str
            fi
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
                if (( mem_tot > 0 && mem_avail > 0 )); then
                    break
                fi
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

    --battery|--battery-percent|--battery-watts|--battery-time)
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

        has_power=false
        has_current=false
        has_energy=false
        has_charge=false
        has_energy_full=false
        has_charge_full=false

        if [[ -n "$bat_dir" ]]; then
            if [[ -f "$bat_dir/power_now" ]]; then
                has_power=true
                [[ -f "$bat_dir/energy_now" ]] && has_energy=true
                [[ -f "$bat_dir/energy_full" ]] && has_energy_full=true
            elif [[ -f "$bat_dir/current_now" && -f "$bat_dir/voltage_now" ]]; then
                has_current=true
                [[ -f "$bat_dir/charge_now" ]] && has_charge=true
                [[ -f "$bat_dir/charge_full" ]] && has_charge_full=true
            fi
        fi

        while true; do
            if [[ -n "$bat_dir" ]]; then
                read -r cap < "$bat_dir/capacity" 2>/dev/null || cap="?"
                read -r stat < "$bat_dir/status" 2>/dev/null || stat="Unknown"
                
                watts_int=0; watts_frac=0
                time_str=""
                
                if [[ "$has_power" == true ]]; then
                    read -r pwr < "$bat_dir/power_now" 2>/dev/null || pwr=0
                    watts_int=$(( pwr / 1000000 ))
                    watts_frac=$(( (pwr % 1000000) / 100000 ))
                    
                    if [[ "$stat" == "Discharging" && "$has_energy" == true ]]; then
                        read -r energy_now < "$bat_dir/energy_now" 2>/dev/null || energy_now=0
                        if (( pwr > 0 )); then
                            total_mins=$(( (energy_now * 60) / pwr ))
                            time_str=$'\n'"$(( total_mins / 60 ))h$(( total_mins % 60 ))m"
                        fi
                    elif [[ "$stat" == "Charging" && "$has_energy_full" == true ]]; then
                        read -r energy_now < "$bat_dir/energy_now" 2>/dev/null || energy_now=0
                        read -r energy_full < "$bat_dir/energy_full" 2>/dev/null || energy_full=0
                        if (( pwr > 0 && energy_full > energy_now )); then
                            total_mins=$(( ((energy_full - energy_now) * 60) / pwr ))
                            time_str=$'\n'"$(( total_mins / 60 ))h$(( total_mins % 60 ))m"
                        fi
                    fi
                    
                elif [[ "$has_current" == true ]]; then
                    read -r curr < "$bat_dir/current_now" 2>/dev/null || curr=0
                    read -r volt < "$bat_dir/voltage_now" 2>/dev/null || volt=0
                    p_uw=$(( (curr / 1000) * (volt / 1000) ))
                    watts_int=$(( p_uw / 1000000 ))
                    watts_frac=$(( (p_uw % 1000000) / 100000 ))
                    
                    if [[ "$stat" == "Discharging" && "$has_charge" == true ]]; then
                        read -r charge_now < "$bat_dir/charge_now" 2>/dev/null || charge_now=0
                        if (( curr > 0 )); then
                            total_mins=$(( (charge_now * 60) / curr ))
                            time_str=$'\n'"$(( total_mins / 60 ))h$(( total_mins % 60 ))m"
                        fi
                    elif [[ "$stat" == "Charging" && "$has_charge_full" == true ]]; then
                        read -r charge_now < "$bat_dir/charge_now" 2>/dev/null || charge_now=0
                        read -r charge_full < "$bat_dir/charge_full" 2>/dev/null || charge_full=0
                        if (( curr > 0 && charge_full > charge_now )); then
                            total_mins=$(( ((charge_full - charge_now) * 60) / curr ))
                            time_str=$'\n'"$(( total_mins / 60 ))h$(( total_mins % 60 ))m"
                        fi
                    fi
                fi
                
                if [[ "$MODE" == "--battery-percent" ]]; then
                    out_str="${cap}%"
                elif [[ "$MODE" == "--battery-watts" ]]; then
                    out_str="${watts_int}.${watts_frac}W"
                elif [[ "$MODE" == "--battery-time" ]]; then
                    if [[ -n "$time_str" ]]; then
                        out_str="${time_str#$'\n'}"
                    else
                        out_str="N/A"
                    fi
                else
                    printf -v out_str "%s%% %d.%dW%s" "$cap" "$watts_int" "$watts_frac" "$time_str"
                fi
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
                case "$d_pid" in
                    ""|*[!0-9]*) ;;
                    *)
                        if kill -0 "$d_pid" 2>/dev/null; then
                            if exec {_gfd}< "/proc/$d_pid/cmdline" 2>/dev/null; then
                                IFS= read -r -d '' _g1 <&"$_gfd" 2>/dev/null || _g1=""
                                IFS= read -r -d '' _g2 <&"$_gfd" 2>/dev/null || _g2=""
                                exec {_gfd}<&- 2>/dev/null
                                [[ "$_g2" == *network_meter_daemon* ]] && kill -USR1 "$d_pid" 2>/dev/null
                            fi
                        fi
                        ;;
                esac
            fi
        fi
        
        while true; do
            [[ -d "$STATE_DIR" ]] && printf "" > "$HEARTBEAT_FILE"
            if [[ -r "$DAEMON_PID_FILE" ]]; then
                read -r _gp < "$DAEMON_PID_FILE" 2>/dev/null || _gp=""
                case "$_gp" in
                    ""|*[!0-9]*) ;;
                    *)
                        if kill -0 "$_gp" 2>/dev/null; then
                            if exec {_gfd}< "/proc/$_gp/cmdline" 2>/dev/null; then
                                IFS= read -r -d '' _g1 <&"$_gfd" 2>/dev/null || _g1=""
                                IFS= read -r -d '' _g2 <&"$_gfd" 2>/dev/null || _g2=""
                                exec {_gfd}<&- 2>/dev/null
                                [[ "$_g2" == *network_meter_daemon* ]] && kill -USR1 "$_gp" 2>/dev/null
                            fi
                        fi
                        ;;
                esac
                unset _gp _g1 _g2 _gfd
            fi
            if [[ -r "$STATE_FILE" ]]; then
                unit=""; up=""; down=""
                for ((_rt=0; _rt<5; _rt++)); do
                    if read -r _u _up _down _c < "$STATE_FILE" 2>/dev/null; then
                        case "${_u:-}" in
                            KB|MB|GB|-)
                                if [[ -n "${_up:-}" && -n "${_down:-}" && -n "${_c:-}" ]]; then
                                    unit="$_u"; up="$_up"; down="$_down"
                                    break
                                fi
                                ;;
                        esac
                    fi
                done
                unset _rt _u _up _down _c
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
        
    --gpu-power)
        card="${2:-}"
        vendor="${3:-}"
        if [[ -z "$card" || -z "$vendor" ]]; then
            send_osd "GPU Err"
            exit 1
        fi
        
        case "${vendor,,}" in
            intel)
                path=""
                for name_file in /sys/devices/virtual/powercap/intel-rapl/intel-rapl:0/*/name; do
                    if [[ -f "$name_file" ]] && [[ "$(cat "$name_file")" == "uncore" ]]; then
                        path="${name_file%/*}/energy_uj"
                        break
                    fi
                done
                
                if [[ -z "$path" || ! -r "$path" ]]; then
                    while true; do
                        send_osd "N/A"
                        sleep 5
                    done
                fi
                
                read -r last_energy < "$path"
                last_time_str="${EPOCHREALTIME//./}"
                
                sleep 1
                
                while true; do
                    if read -r current_energy < "$path" 2>/dev/null; then
                        curr_time_str="${EPOCHREALTIME//./}"
                        delta_energy=$((current_energy - last_energy))
                        delta_time_us=$((curr_time_str - last_time_str))
                        if (( delta_time_us > 0 )); then
                            if (( delta_energy < 0 )); then
                                max_range=4294967296
                                read -r max_range < "${path%/*}/max_energy_range_uj" 2>/dev/null || true
                                delta_energy=$(( delta_energy + max_range ))
                            fi
                            watts_x10=$(( (delta_energy * 10) / delta_time_us ))
                            watts_int=$(( watts_x10 / 10 ))
                            watts_frac=$(( watts_x10 % 10 ))
                            send_osd "${watts_int}.${watts_frac}W"
                        fi
                        last_energy=$current_energy
                        last_time_str=$curr_time_str
                    fi
                    sleep 1
                done
                ;;
                
            amd)
                path=""
                for f in /sys/class/drm/"$card"/device/hwmon/hwmon*/power1_average /sys/class/drm/"$card"/device/hwmon/hwmon*/power1_input; do
                    if [[ -f "$f" ]]; then
                        path="$f"
                        break
                    fi
                done
                
                if [[ -z "$path" || ! -r "$path" ]]; then
                    while true; do
                        send_osd "N/A"
                        sleep 5
                    done
                fi
                
                while true; do
                    if read -r microwatts < "$path" 2>/dev/null; then
                        watts_x10=$(( microwatts / 100000 ))
                        watts_int=$(( watts_x10 / 10 ))
                        watts_frac=$(( watts_x10 % 10 ))
                        send_osd "${watts_int}.${watts_frac}W"
                    else
                        send_osd "N/A"
                    fi
                    sleep 1
                done
                ;;
                
            nvidia)
                pstate_path="/sys/class/drm/$card/device/power_state"
                while true; do
                    pstate=""
                    [[ -r "$pstate_path" ]] && read -r pstate < "$pstate_path" 2>/dev/null
                    if [[ "$pstate" == D3* ]]; then
                        send_osd "D3"
                    else
                        power_str=$(nvidia-smi --query-gpu=power.draw --format=csv,noheader,nounits 2>/dev/null || echo "N/A")
                        if [[ "$power_str" != "N/A" ]]; then
                            if [[ "$power_str" =~ ^([0-9]+)\.([0-9]) ]]; then
                                send_osd "${BASH_REMATCH[1]}.${BASH_REMATCH[2]}W"
                            else
                                send_osd "${power_str}W"
                            fi
                        else
                            send_osd "N/A"
                        fi
                    fi
                    sleep 1
                done
                ;;
                
            *)
                send_osd "N/A"
                exit 1
                ;;
        esac
        ;;

    --gpu-usage)
        card="${2:-}"
        vendor="${3:-}"
        if [[ -z "$card" || -z "$vendor" ]]; then
            send_osd "GPU Err"
            exit 1
        fi
        
        case "${vendor,,}" in
            intel)
                path="/sys/class/drm/$card/device/drm/$card/power/rc6_residency_ms"
                if [[ ! -r "$path" ]]; then
                    while true; do
                        send_osd "N/A"
                        sleep 5
                    done
                fi
                
                read -r last_rc6 < "$path"
                last_time_str="${EPOCHREALTIME//./}"
                last_time_ms=$(( last_time_str / 1000 ))
                
                sleep 1
                
                while true; do
                    if read -r current_rc6 < "$path" 2>/dev/null; then
                        curr_time_str="${EPOCHREALTIME//./}"
                        curr_time_ms=$(( curr_time_str / 1000 ))
                        delta_rc6=$((current_rc6 - last_rc6))
                        delta_time=$((curr_time_ms - last_time_ms))
                        if (( delta_time > 0 )); then
                            usage=$(( 100 * (delta_time - delta_rc6) / delta_time ))
                            (( usage < 0 )) && usage=0
                            (( usage > 100 )) && usage=100
                            send_osd "${usage}%"
                        fi
                        last_rc6=$current_rc6
                        last_time_ms=$curr_time_ms
                    fi
                    sleep 1
                done
                ;;
                
            amd)
                path="/sys/class/drm/$card/device/gpu_busy_percent"
                if [[ ! -r "$path" ]]; then
                    while true; do
                        send_osd "N/A"
                        sleep 5
                    done
                fi
                
                while true; do
                    if read -r usage < "$path" 2>/dev/null; then
                        send_osd "${usage}%"
                    else
                        send_osd "N/A"
                    fi
                    sleep 1
                done
                ;;
                
            nvidia)
                pstate_path="/sys/class/drm/$card/device/power_state"
                while true; do
                    pstate=""
                    [[ -r "$pstate_path" ]] && read -r pstate < "$pstate_path" 2>/dev/null
                    if [[ "$pstate" == D3* ]]; then
                        send_osd "D3"
                    else
                        usage=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null || echo "N/A")
                        if [[ "$usage" != "N/A" ]]; then
                            send_osd "${usage}%"
                        else
                            send_osd "N/A"
                        fi
                    fi
                    sleep 1
                done
                ;;
                
            *)
                send_osd "N/A"
                exit 1
                ;;
        esac
        ;;

    --gpu-mem)
        card="${2:-}"
        vendor="${3:-}"
        if [[ -z "$card" || -z "$vendor" ]]; then
            send_osd "GPU Err"
            exit 1
        fi
        
        case "${vendor,,}" in
            intel)
                while true; do
                    # Pure Bash proc fdinfo scanner for user DRM memory (no subprocesses)
                    declare -A client_mem=()
                    while read -r f; do
                        [[ -r "$f" ]] || continue
                        driver=""
                        client_id=""
                        total_sys=0
                        total_vram=0
                        
                        # || [[ -n "$name" ]] handles EOF without trailing newline
                        # || true prevents set -e crash if process exits mid-read
                        while read -r name val unit || [[ -n "$name" ]]; do
                            case "$name" in
                                drm-driver:) driver="$val" ;;
                                drm-client-id:) client_id="$val" ;;
                                drm-total-system0:|drm-total-system:)
                                    total_sys="$val"
                                    [[ "$unit" == "KiB" ]] && total_sys=$((val * 1024))
                                    ;;
                                drm-total-vram:)
                                    total_vram="$val"
                                    [[ "$unit" == "KiB" ]] && total_vram=$((val * 1024))
                                    ;;
                            esac
                        done < "$f" 2>/dev/null || true
                        
                        if [[ -n "$driver" && -n "$client_id" ]]; then
                            total=$((total_sys + total_vram))
                            key="${driver}_${client_id}"
                            if [[ -z "${client_mem[$key]:-}" ]] || (( total > client_mem[$key] )); then
                                client_mem["$key"]=$total
                            fi
                        fi
                    done < <(grep -l "drm-driver" /proc/[0-9]*/fdinfo/* 2>/dev/null || true)
                    
                    sum=0
                    for key in "${!client_mem[@]}"; do
                        sum=$((sum + client_mem[$key]))
                    done
                    
                    send_osd "$((sum / 1048576))MB"
                    sleep 1
                done
                ;;
                
            amd)
                used_path="/sys/class/drm/$card/device/mem_info_vram_used"
                if [[ ! -r "$used_path" ]]; then
                    send_osd "N/A"
                    exit 1
                fi
                
                while true; do
                    if read -r used < "$used_path" 2>/dev/null; then
                        used_mb=$(( used / 1048576 ))
                        send_osd "${used_mb}MB"
                    else
                        send_osd "N/A"
                    fi
                    sleep 1
                done
                ;;
                
            nvidia)
                pstate_path="/sys/class/drm/$card/device/power_state"
                while true; do
                    pstate=""
                    [[ -r "$pstate_path" ]] && read -r pstate < "$pstate_path" 2>/dev/null
                    if [[ "$pstate" == D3* ]]; then
                        send_osd "D3"
                    else
                        mem_str=$(nvidia-smi --query-gpu=memory.total,memory.free --format=csv,noheader,nounits 2>/dev/null || echo "N/A")
                        if [[ "$mem_str" != "N/A" ]]; then
                            total=$(echo "$mem_str" | cut -d, -f1 | tr -d ' ')
                            free=$(echo "$mem_str" | cut -d, -f2 | tr -d ' ')
                            used=$((total - free))
                            send_osd "${used}MB"
                        else
                            send_osd "N/A"
                        fi
                    fi
                    sleep 1
                done
                ;;
                
            *)
                send_osd "N/A"
                exit 1
                ;;
        esac
        ;;

    --hud)
        card="${2:-}"
        vendor="${3:-}"
        
        cpu_zone_file=""
        for hwmon in /sys/class/hwmon/hwmon*/name; do
            [[ -r "$hwmon" ]] || continue
            read -r name < "$hwmon" 2>/dev/null || continue
            if [[ "$name" == "coretemp" || "$name" == "k10temp" || "$name" == "zenpower" || "$name" == "cpu_thermal" ]]; then
                dir="${hwmon%/*}"
                if [[ -r "$dir/temp1_input" ]]; then
                    cpu_zone_file="$dir/temp1_input"
                    break
                fi
            fi
        done
        if [[ -z "$cpu_zone_file" ]]; then
            for tz in /sys/class/thermal/thermal_zone*/type; do
                [[ -r "$tz" ]] || continue
                read -r type < "$tz" 2>/dev/null || continue
                if [[ "$type" == *"x86_pkg_temp"* || "$type" == *"cpu"* ]]; then
                    dir="${tz%/*}"
                    if [[ -r "$dir/temp" ]]; then
                        cpu_zone_file="$dir/temp"
                        break
                    fi
                fi
            done
        fi
        
        cpu_energy_path="/sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj"
        has_cpu_power=false
        if [[ -r "$cpu_energy_path" ]]; then
            has_cpu_power=true
        fi
        
        nv_pstate_path=""
        if [[ "${vendor,,}" == "nvidia" ]]; then
            nv_pstate_path="/sys/class/drm/$card/device/power_state"
        fi
        
        amd_pwr_path=""
        if [[ "${vendor,,}" == "amd" ]]; then
            for f in /sys/class/drm/"$card"/device/hwmon/hwmon*/power1_average /sys/class/drm/"$card"/device/hwmon/hwmon*/power1_input; do
                if [[ -f "$f" ]]; then
                    amd_pwr_path="$f"
                    break
                fi
            done
        fi
        
        intel_pwr_path=""
        if [[ "${vendor,,}" == "intel" ]]; then
            for name_file in /sys/devices/virtual/powercap/intel-rapl/intel-rapl:0/*/name; do
                if [[ -f "$name_file" ]]; then
                    name_val=$(cat "$name_file" 2>/dev/null || echo "")
                    if [[ "$name_val" == "uncore" ]]; then
                        intel_pwr_path="${name_file%/*}/energy_uj"
                        break
                    fi
                fi
            done
        fi
        
        amd_busy_path=""
        if [[ "${vendor,,}" == "amd" ]]; then
            amd_busy_path="/sys/class/drm/$card/device/gpu_busy_percent"
        fi
        
        intel_rc6_path=""
        if [[ "${vendor,,}" == "intel" ]]; then
            intel_rc6_path="/sys/class/drm/$card/device/drm/$card/power/rc6_residency_ms"
        fi
        
        amd_vram_path=""
        if [[ "${vendor,,}" == "amd" ]]; then
            amd_vram_path="/sys/class/drm/$card/device/mem_info_vram_used"
        fi
        
        gpu_temp_files=()
        if [[ -n "$card" ]]; then
            for tfile in /sys/class/drm/"$card"/device/hwmon/hwmon*/temp*_input; do
                [[ -f "$tfile" ]] && gpu_temp_files+=("$tfile")
            done
        fi
        
        prev_idle=0; prev_total=0
        last_cpu_energy=0
        last_cpu_time=0
        if [[ "$has_cpu_power" == true ]]; then
            read -r last_cpu_energy < "$cpu_energy_path"
            last_cpu_time="${EPOCHREALTIME//./}"
        fi
        
        last_gpu_energy=0
        last_gpu_time=0
        if [[ -n "$intel_pwr_path" && -r "$intel_pwr_path" ]]; then
            read -r last_gpu_energy < "$intel_pwr_path"
            last_gpu_time="${EPOCHREALTIME//./}"
        fi
        
        last_rc6=0
        last_time_ms=0
        if [[ -n "$intel_rc6_path" && -r "$intel_rc6_path" ]]; then
            read -r last_rc6 < "$intel_rc6_path"
            last_rc6_time_str="${EPOCHREALTIME//./}"
            last_time_ms=$(( last_rc6_time_str / 1000 ))
        fi
        
        sleep 1
        
        while true; do
            read -r _ user nice system idle iowait irq softirq steal _ < /proc/stat
            total=$((user + nice + system + idle + iowait + irq + softirq + steal))
            diff_idle=$((idle - prev_idle))
            diff_total=$((total - prev_total))
            cpu_usage=0
            if (( prev_total > 0 && diff_total > 0 )); then
                cpu_usage=$(( 100 * (diff_total - diff_idle) / diff_total ))
            fi
            prev_idle=$idle
            prev_total=$total
            
            cpu_watts="N/A"
            if [[ "$has_cpu_power" == true ]]; then
                if read -r current_cpu_energy < "$cpu_energy_path" 2>/dev/null; then
                    curr_cpu_time="${EPOCHREALTIME//./}"
                    delta_energy=$((current_cpu_energy - last_cpu_energy))
                    delta_time_us=$((curr_cpu_time - last_cpu_time))
                    if (( delta_time_us > 0 )); then
                        if (( delta_energy < 0 )); then
                            max_range=4294967296
                            read -r max_range < "${cpu_energy_path%/*}/max_energy_range_uj" 2>/dev/null || true
                            delta_energy=$(( delta_energy + max_range ))
                        fi
                        watts_x10=$(( (delta_energy * 10) / delta_time_us ))
                        cpu_watts="$(( watts_x10 / 10 )).$(( watts_x10 % 10 ))W"
                    fi
                    last_cpu_energy=$current_cpu_energy
                    last_cpu_time=$curr_cpu_time
                fi
            fi
            
            cpu_temp="N/A"
            if [[ -n "$cpu_zone_file" ]] && read -r t < "$cpu_zone_file" 2>/dev/null; then
                cpu_temp="$(( t / 1000 ))°C"
            fi
            
            mem_tot=0; mem_avail=0
            while read -r key val _; do
                case "$key" in
                    MemTotal:) mem_tot=$val ;;
                    MemAvailable:) mem_avail=$val ;;
                esac
                if (( mem_tot > 0 && mem_avail > 0 )); then
                    break
                fi
            done < /proc/meminfo
            ram_used_mb=$(( (mem_tot - mem_avail) / 1024 ))
            ram_used_gb=$(( ram_used_mb / 1024 ))
            ram_used_gb_frac=$(( (ram_used_mb % 1024) * 10 / 1024 ))
            ram_str="${ram_used_gb}.${ram_used_gb_frac}GB"
            
            gpu_usage="N/A"
            gpu_watts="N/A"
            gpu_vram="N/A"
            gpu_temp="N/A"
            
            if [[ ${#gpu_temp_files[@]} -gt 0 ]]; then
                if read -r gt < "${gpu_temp_files[0]}" 2>/dev/null; then
                    gpu_temp="$((gt/1000))°C"
                fi
            fi
            
            case "${vendor,,}" in
                intel)
                    if [[ -n "$intel_pwr_path" && -r "$intel_pwr_path" ]]; then
                        if read -r current_gpu_energy < "$intel_pwr_path" 2>/dev/null; then
                            curr_gpu_time="${EPOCHREALTIME//./}"
                            delta_energy=$((current_gpu_energy - last_gpu_energy))
                            delta_time_us=$((curr_gpu_time - last_gpu_time))
                            if (( delta_time_us > 0 )); then
                                if (( delta_energy < 0 )); then
                                    delta_energy=$(( delta_energy + 4294967296 ))
                               fi
                               watts_x10=$(( (delta_energy * 10) / delta_time_us ))
                               gpu_watts="$(( watts_x10 / 10 )).$(( watts_x10 % 10 ))W"
                            fi
                            last_gpu_energy=$current_gpu_energy
                            last_gpu_time=$curr_gpu_time
                        fi
                    fi
                    
                    if [[ -n "$intel_rc6_path" && -r "$intel_rc6_path" ]]; then
                        if read -r current_rc6 < "$intel_rc6_path" 2>/dev/null; then
                            curr_time_str="${EPOCHREALTIME//./}"
                            curr_time_ms=$(( curr_time_str / 1000 ))
                            delta_rc6=$((current_rc6 - last_rc6))
                            delta_time=$((curr_time_ms - last_time_ms))
                            if (( delta_time > 0 )); then
                                u=$(( 100 * (delta_time - delta_rc6) / delta_time ))
                                (( u < 0 )) && u=0
                                (( u > 100 )) && u=100
                                gpu_usage="${u}%"
                            fi
                            last_rc6=$current_rc6
                            last_time_ms=$curr_time_ms
                        fi
                    fi
                    
                    declare -A client_mem=()
                    while read -r f; do
                        [[ -r "$f" ]] || continue
                        driver=""
                        client_id=""
                        total_sys=0
                        total_vram=0
                        while read -r name val unit || [[ -n "$name" ]]; do
                            case "$name" in
                                drm-driver:) driver="$val" ;;
                                drm-client-id:) client_id="$val" ;;
                                drm-total-system0:|drm-total-system:)
                                    total_sys="$val"
                                    [[ "$unit" == "KiB" ]] && total_sys=$((val * 1024))
                                    ;;
                                drm-total-vram:)
                                    total_vram="$val"
                                    [[ "$unit" == "KiB" ]] && total_vram=$((val * 1024))
                                    ;;
                            esac
                        done < "$f" 2>/dev/null || true
                        if [[ -n "$driver" && -n "$client_id" ]]; then
                            total=$((total_sys + total_vram))
                            key="${driver}_${client_id}"
                            if [[ -z "${client_mem[$key]:-}" ]] || (( total > client_mem[$key] )); then
                                client_mem["$key"]=$total
                            fi
                        fi
                    done < <(grep -l "drm-driver" /proc/[0-9]*/fdinfo/* 2>/dev/null || true)
                    sum=0
                    for key in "${!client_mem[@]}"; do
                        sum=$((sum + client_mem[$key]))
                    done || true
                    vram_mb=$((sum / 1048576))
                    vram_gb=$(( vram_mb / 1024 ))
                    vram_gb_frac=$(( (vram_mb % 1024) * 10 / 1024 ))
                    gpu_vram="${vram_gb}.${vram_gb_frac}GB"
                    ;;
                    
                amd)
                    if [[ -n "$amd_pwr_path" && -r "$amd_pwr_path" ]]; then
                        if read -r microwatts < "$amd_pwr_path" 2>/dev/null; then
                            watts_x10=$(( microwatts / 100000 ))
                            gpu_watts="$(( watts_x10 / 10 )).$(( watts_x10 % 10 ))W"
                        fi
                    fi
                    
                    if [[ -n "$amd_busy_path" && -r "$amd_busy_path" ]]; then
                        if read -r u < "$amd_busy_path" 2>/dev/null; then
                            gpu_usage="${u}%"
                        fi
                    fi
                    
                    if [[ -n "$amd_vram_path" && -r "$amd_vram_path" ]]; then
                        if read -r vram_bytes < "$amd_vram_path" 2>/dev/null; then
                            vram_mb=$(( vram_bytes / 1048576 ))
                            vram_gb=$(( vram_mb / 1024 ))
                            vram_gb_frac=$(( (vram_mb % 1024) * 10 / 1024 ))
                            gpu_vram="${vram_gb}.${vram_gb_frac}GB"
                        fi
                    fi
                    ;;
                    
                nvidia)
                    pstate=""
                    [[ -n "$nv_pstate_path" && -r "$nv_pstate_path" ]] && read -r pstate < "$nv_pstate_path" 2>/dev/null
                    if [[ "$pstate" == D3* ]]; then
                        gpu_usage="D3"
                        gpu_watts="D3"
                        gpu_vram="D3"
                        gpu_temp="D3"
                    else
                        nv_info=$(nvidia-smi --query-gpu=power.draw,utilization.gpu,memory.total,memory.free,temperature.gpu --format=csv,noheader,nounits 2>/dev/null || echo "N/A")
                        if [[ "$nv_info" != "N/A" ]]; then
                            IFS=',' read -r nv_pwr nv_usg nv_mem_tot nv_mem_free nv_tmp <<< "$nv_info"
                            nv_pwr=$(echo "$nv_pwr" | tr -d ' ')
                            nv_usg=$(echo "$nv_usg" | tr -d ' ')
                            nv_mem_tot=$(echo "$nv_mem_tot" | tr -d ' ')
                            nv_mem_free=$(echo "$nv_mem_free" | tr -d ' ')
                            nv_tmp=$(echo "$nv_tmp" | tr -d ' ')
                            
                            if [[ "$nv_pwr" =~ ^([0-9]+)\.([0-9]) ]]; then
                                gpu_watts="${BASH_REMATCH[1]}.${BASH_REMATCH[2]}W"
                            else
                                gpu_watts="${nv_pwr}W"
                            fi
                            gpu_usage="${nv_usg}%"
                            if [[ -n "$nv_mem_tot" && -n "$nv_mem_free" ]]; then
                                nv_used=$((nv_mem_tot - nv_mem_free))
                                nv_used_gb=$(( nv_used / 1024 ))
                                nv_used_gb_frac=$(( (nv_used % 1024) * 10 / 1024 ))
                                gpu_vram="${nv_used_gb}.${nv_used_gb_frac}GB"
                            fi
                            gpu_temp="${nv_tmp}°C"
                        fi
                    fi
                    ;;
            esac
            
            hud_body="  ${cpu_usage}% • ${cpu_watts} • ${cpu_temp}\n󰢮  ${gpu_usage} • ${gpu_watts} • ${gpu_temp}\n󰘚  ${ram_str} | VRAM ${gpu_vram}"
            send_hud_osd "$hud_body"
            
            sleep 1
        done
        ;;

    --workspace)
        if [[ -z "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]]; then
            send_osd "WS: ?"
            exit 1
        fi
        
        if ws_info=$(hyprctl activeworkspace 2>/dev/null); then
            if [[ "$ws_info" =~ "workspace ID "([0-9\-]+) ]]; then
                ws_id="${BASH_REMATCH[1]}"
            else
                ws_id="?"
            fi
            send_osd "WS: $ws_id"
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
                    if [[ "$ws_info" =~ "workspace ID "([0-9\-]+) ]]; then
                        ws_id="${BASH_REMATCH[1]}"
                    else
                        ws_id="?"
                    fi
                    send_osd "WS: $ws_id"
                fi
                sleep 1
            done
        fi
        ;;
esac
