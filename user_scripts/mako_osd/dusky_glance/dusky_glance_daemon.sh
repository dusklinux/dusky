#!/usr/bin/env bash
# ==============================================================================
# DUSKY GLANCE DAEMON - HIGH-PERFORMANCE & RELIABILITY EDITION
# ==============================================================================

set -euo pipefail

RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$UID}"
GLANCE_STATE_DIR="$RUNTIME_DIR/dusky-glance"
MODE="${1:-}"

die() {
    printf 'dusky-glance: %s\n' "$*" >&2
    exit 1
}

usage() {
    printf 'Usage: %s --mode [arguments]\n' "$0" >&2
    exit 2
}

[[ -n "$MODE" ]] || usage
[[ -d "$RUNTIME_DIR" && -w "$RUNTIME_DIR" ]] ||
    die "Runtime directory unavailable: $RUNTIME_DIR"

normalize_seconds() {
    local value="$1"
    local minimum="$2"

    [[ "$value" =~ ^[0-9]+$ ]] ||
        die "Duration must contain decimal digits only"

    while [[ ${#value} -gt 1 && "$value" == 0* ]]; do
        value="${value#0}"
    done

    (( ${#value} <= 10 )) || die "Duration is too large"

    value=$((10#$value))

    (( value >= minimum && value <= 2147483647 )) ||
        die "Duration is outside the supported range"

    printf '%s\n' "$value"
}

case "$MODE" in
    --stop|--stop-all)
        (( $# == 1 )) || usage
        ;;

    --clock|--clock-short|--stopwatch|--cpu-power|--cpu|--ram|\
    --ram-temp|--zram|--temp|--battery|--battery-percent|\
    --battery-watts|--battery-time|--disk|--network|--uptime|--workspace)
        (( $# == 1 )) || usage
        ;;

    --timer)
        (( $# <= 2 )) || usage
        duration=$(normalize_seconds "${2:-900}" 1)
        set -- "$MODE" "$duration"
        ;;

    --pomodoro)
        (( $# <= 3 )) || usage
        work=$(normalize_seconds "${2:-1500}" 1)
        rest=$(normalize_seconds "${3:-300}" 0)
        set -- "$MODE" "$work" "$rest"
        ;;

    --world-clock)
        (( $# >= 2 && $# <= 3 )) || usage

        [[ "$2" != /* && "$2" != *".."* &&
           -f "/usr/share/zoneinfo/$2" ]] ||
            die "Expected an installed timezone such as Asia/Kolkata"

        set -- "$MODE" "$2" "${3:-Time}"
        ;;

    --disk-read|--disk-write|--disk-temp)
        (( $# == 2 )) || usage

        [[ "$2" =~ ^[[:alnum:]_.+-]+$ ]] ||
            die "Invalid block-device name: $2"
        ;;

    --gpu-power|--gpu-usage|--gpu-mem)
        (( $# == 3 )) || usage

        [[ "$2" =~ ^card[0-9]+$ ]] ||
            die "Expected a DRM card name such as card0"

        case "${3,,}" in
            intel|amd|nvidia) ;;
            *) die "Unsupported GPU vendor: $3" ;;
        esac

        set -- "$MODE" "$2" "${3,,}"
        ;;

    --hud)
        if (( $# != 1 && $# != 3 )); then
            usage
        fi

        if (( $# == 3 )); then
            [[ "$2" =~ ^card[0-9]+$ ]] ||
                die "Expected a DRM card name such as card0"

            case "${3,,}" in
                intel|amd|nvidia) ;;
                *) die "Unsupported GPU vendor: $3" ;;
            esac

            set -- "$MODE" "$2" "${3,,}"
        fi
        ;;

    *)
        die "Unknown mode: $MODE"
        ;;
esac

for required_cmd in flock busctl sha256sum timeout; do
    command -v "$required_cmd" >/dev/null 2>&1 ||
        die "Missing required command: $required_cmd"
done

mkdir -p -- "$GLANCE_STATE_DIR"

# --- SHARED PROCESS & INSTANCE CONTROL ---

process_start() {
    local pid="$1"
    local raw
    local -a fields

    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    (( pid > 1 )) || return 1

    { IFS= read -r raw < "/proc/$pid/stat"; } 2>/dev/null ||
        return 1

    # Strip through the final ") " after process command name
    read -r -a fields <<< "${raw##*) }"

    (( ${#fields[@]} >= 20 )) || return 1
    [[ "${fields[0]}" != Z && "${fields[0]}" != X ]] ||
        return 1

    printf '%s\n' "${fields[19]}"
}

same_process() {
    local actual_start
    actual_start=$(process_start "$1") || return 1
    [[ "$actual_start" == "$2" ]]
}

stop_record() {
    local file="$1"
    local pid="" started=""
    local attempt

    if ! { read -r pid started < "$file"; } 2>/dev/null; then
        rm -f -- "$file"
        return 0
    fi

    if ! same_process "$pid" "$started"; then
        rm -f -- "$file"
        return 0
    fi

    # Send SIGTERM to the daemon to trigger standard exit traps and notification cleanup.
    kill -TERM "$pid" 2>/dev/null || true

    # Allow generous time (up to 8.0s) for any bounded query to abort and cleanup to execute D-Bus calls
    for ((attempt = 0; attempt < 80; attempt++)); do
        if ! same_process "$pid" "$started"; then
            rm -f -- "$file"
            return 0
        fi
        sleep 0.1
    done

    # If the process is still running after the full grace window, preserve the record and report failure
    printf 'dusky-glance: Process %s did not terminate within grace period; retaining record\n' "$pid" >&2
    return 1
}

# Serialize start and stop operations using advisory lock
exec 9>"$GLANCE_STATE_DIR/control.lock"
flock -x 9

if [[ "$MODE" == "--stop" || "$MODE" == "--stop-all" ]]; then
    result=0

    for record in "$GLANCE_STATE_DIR"/*.pid; do
        [[ -f "$record" ]] || continue
        stop_record "$record" || result=1
    done

    exit "$result"
fi

case "$MODE" in
    --timer|--pomodoro)
        command -v notify-send >/dev/null 2>&1 ||
            die "Missing command: notify-send"
        ;;
esac

MODE_BASE="${MODE#--}"
instance_hash=$(printf '%s\0' "$@" | sha256sum)
instance_hash="${instance_hash%% *}"

MODE_SLUG="${MODE_BASE}-${instance_hash}"
CURRENT_APP="dusky-glance-${MODE_BASE}"
PID_FILE="$GLANCE_STATE_DIR/${MODE_SLUG}.pid"

if [[ -f "$PID_FILE" ]]; then
    old_pid=""
    old_start=""

    if { read -r old_pid old_start < "$PID_FILE"; } 2>/dev/null &&
       same_process "$old_pid" "$old_start"; then
        stop_record "$PID_FILE"
        exit 0
    fi

    rm -f -- "$PID_FILE"
fi

case "$MODE" in
    --disk-read|--disk-write|--disk-temp)
        [[ -d "/sys/class/block/$2" ]] ||
            die "Unknown block device: $2"
        ;;
esac

MY_PID=$BASHPID
MY_START=$(process_start "$MY_PID")
OSD_ID=0
OSD_OWNER=""
LAST_NOTIFY_WARNING=-30

warn_notification() {
    if (( SECONDS - LAST_NOTIFY_WARNING >= 30 )); then
        printf 'dusky-glance: notification delivery failed; retrying\n' >&2
        LAST_NOTIFY_WARNING=$SECONDS
    fi
    return 0
}

notification_owner() {
    local reply kind owner

    if ! reply=$(busctl --user --timeout=2 -- call \
        org.freedesktop.DBus /org/freedesktop/DBus \
        org.freedesktop.DBus GetNameOwner \
        s org.freedesktop.Notifications 2>/dev/null); then

        busctl --user --timeout=3 -- call \
            org.freedesktop.DBus /org/freedesktop/DBus \
            org.freedesktop.DBus StartServiceByName \
            su org.freedesktop.Notifications 0 \
            >/dev/null 2>&1 || return 1

        reply=$(busctl --user --timeout=2 -- call \
            org.freedesktop.DBus /org/freedesktop/DBus \
            org.freedesktop.DBus GetNameOwner \
            s org.freedesktop.Notifications 2>/dev/null) ||
            return 1
    fi

    read -r kind owner <<< "$reply"
    [[ "$kind" == s ]] || return 1

    owner="${owner#\"}"
    owner="${owner%\"}"

    [[ "$owner" == :* ]] || return 1
    printf '%s\n' "$owner"
}

clear_osd() {
    # Close notification strictly on the owner that issued OSD_ID to avoid cross-server dismissal
    if [[ -n "$OSD_OWNER" ]] && (( OSD_ID > 0 )); then
        busctl --user --timeout=2 -- call \
            "$OSD_OWNER" /org/freedesktop/Notifications \
            org.freedesktop.Notifications CloseNotification \
            u "$OSD_ID" >/dev/null 2>&1 || true
    fi

    OSD_ID=0
}

cleanup() {
    local pid="" started=""

    clear_osd

    if [[ -f "$PID_FILE" ]] &&
       { read -r pid started < "$PID_FILE"; } 2>/dev/null &&
       [[ "$pid" == "$MY_PID" && "$started" == "$MY_START" ]]; then
        rm -f -- "$PID_FILE"
    fi
}

trap cleanup EXIT
trap 'exit 0' INT TERM

printf '%s %s\n' "$MY_PID" "$MY_START" > "$PID_FILE"

flock -u 9
exec 9>&-

# --- NOTIFICATION DISPATCHERS ---

send_osd() {
    local text="$1"
    local font="${2:-monospace 20}"
    local reply kind new_id

    if [[ -z "$OSD_OWNER" ]]; then
        if ! OSD_OWNER=$(notification_owner); then
            OSD_OWNER=""
            warn_notification
            return 0
        fi
        OSD_ID=0
    fi

    # Escape raw data characters before Pango markup wrapping
    text="${text//&/'&amp;'}"
    text="${text//</'&lt;'}"
    text="${text//>/'&gt;'}"

    local body="<span font='${font}' weight='bold'>${text}</span>"

    if ! reply=$(busctl --user --timeout=3 -- call \
        "$OSD_OWNER" /org/freedesktop/Notifications \
        org.freedesktop.Notifications Notify \
        'susssasa{sv}i' \
        "$CURRENT_APP" "$OSD_ID" "" " " "$body" \
        0 0 15000 2>/dev/null); then

        OSD_OWNER=""
        OSD_ID=0
        warn_notification
        return 0
    fi

    read -r kind new_id <<< "$reply"

    if [[ "$kind" == u && "$new_id" =~ ^[0-9]+$ ]]; then
        OSD_ID="$new_id"
    else
        OSD_OWNER=""
        OSD_ID=0
        warn_notification
    fi

    return 0
}

send_hud_osd() {
    send_osd "$1" "monospace 9"
}

send_world_clock_osd() {
    local time_str="$1"
    local place_lbl="$2"
    local diff_lbl="$3"
    local reply kind new_id

    if [[ -z "$OSD_OWNER" ]]; then
        if ! OSD_OWNER=$(notification_owner); then
            OSD_OWNER=""
            warn_notification
            return 0
        fi
        OSD_ID=0
    fi

    time_str="${time_str//&/'&amp;'}"
    time_str="${time_str//</'&lt;'}"
    time_str="${time_str//>/'&gt;'}"

    place_lbl="${place_lbl//&/'&amp;'}"
    place_lbl="${place_lbl//</'&lt;'}"
    place_lbl="${place_lbl//>/'&gt;'}"

    diff_lbl="${diff_lbl//&/'&amp;'}"
    diff_lbl="${diff_lbl//</'&lt;'}"
    diff_lbl="${diff_lbl//>/'&gt;'}"

    local body="<span font='monospace 11' weight='bold'>${time_str}</span>"$'\n'"<span font='monospace 9'>${place_lbl} • ${diff_lbl}</span>"

    if ! reply=$(busctl --user --timeout=3 -- call \
        "$OSD_OWNER" /org/freedesktop/Notifications \
        org.freedesktop.Notifications Notify \
        'susssasa{sv}i' \
        "$CURRENT_APP" "$OSD_ID" "" " " "$body" \
        0 0 15000 2>/dev/null); then

        OSD_OWNER=""
        OSD_ID=0
        warn_notification
        return 0
    fi

    read -r kind new_id <<< "$reply"

    if [[ "$kind" == u && "$new_id" =~ ^[0-9]+$ ]]; then
        OSD_ID="$new_id"
    else
        OSD_OWNER=""
        OSD_ID=0
        warn_notification
    fi

    return 0
}

# --- TIMING & SYSTEM HELPERS ---

monotonic_us() {
    local -n _out_us=$1
    local value whole fraction

    { read -r value _ < /proc/uptime; } 2>/dev/null || return 1

    whole="${value%%.*}"
    fraction="${value#*.}000000"
    fraction="${fraction:0:6}"

    _out_us=$((10#$whole * 1000000 + 10#$fraction))
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

send_alert_notification() {
    local tag="$1" msg="$2"
    if ! timeout --kill-after=1s 3s notify-send \
        -u critical \
        -a "dusky-glance-alert" \
        -h string:x-canonical-private-synchronous:"${tag}-${MY_PID}" \
        "$msg" 2>/dev/null; then
        printf 'dusky-glance: Alert notification delivery failed ("%s")\n' "$msg" >&2
    fi
}

offset_to_minutes() {
    local -n _out_min=$1
    local offset="$2"
    local hours minutes

    [[ "$offset" =~ ^[+-][0-9]{4}$ ]] || return 1

    hours=$((10#${offset:1:2}))
    minutes=$((10#${offset:3:2}))

    _out_min=$((hours * 60 + minutes))
    [[ "${offset:0:1}" == "-" ]] && _out_min=$((-_out_min))

    return 0
}

NVIDIA_PCI_ID=""

init_nvidia_device() {
    local card_node="$1"
    local resolved cand
    resolved=$(readlink -f -- "/sys/class/drm/$card_node/device" 2>/dev/null) || return 1
    cand="${resolved##*/}"
    if [[ "$cand" =~ ^[[:xdigit:]]{4}:[[:xdigit:]]{2}:[[:xdigit:]]{2}\.[0-7]$ ]]; then
        NVIDIA_PCI_ID="$cand"
        return 0
    fi
    return 1
}

is_nvidia_suspended() {
    local card_node="$1"
    local pstate="" rt_status=""
    local dev_dir="/sys/class/drm/$card_node/device"

    if [[ -r "$dev_dir/power_state" ]]; then
        { read -r pstate < "$dev_dir/power_state"; } 2>/dev/null || pstate=""
    fi
    if [[ -r "$dev_dir/power/runtime_status" ]]; then
        { read -r rt_status < "$dev_dir/power/runtime_status"; } 2>/dev/null || rt_status=""
    fi

    [[ "$pstate" == D3* || "$rt_status" == "suspended" ]]
}

query_nvidia() {
    timeout --kill-after=1s 3s nvidia-smi \
        --id="$NVIDIA_PCI_ID" \
        --query-gpu="$1" \
        --format=csv,noheader,nounits 2>/dev/null
}

find_system_battery() {
    local candidate
    for candidate in /sys/class/power_supply/*; do
        [[ -d "$candidate" ]] || continue

        local type_val="" scope_val="" present_val=""
        { read -r type_val < "$candidate/type"; } 2>/dev/null || continue
        [[ "$type_val" == "Battery" ]] || continue

        # Exclude peripheral devices (mice, keyboards, etc.)
        { read -r scope_val < "$candidate/scope"; } 2>/dev/null || true
        [[ "$scope_val" == "Device" ]] && continue

        # Ensure battery is present
        { read -r present_val < "$candidate/present"; } 2>/dev/null || true
        [[ "$present_val" == "0" ]] && continue

        printf '%s\n' "$candidate"
        return 0
    done
    return 1
}

find_cpu_temp_sensor() {
    local hwmon dir name tfile tz type
    for hwmon in /sys/class/hwmon/hwmon*/name; do
        [[ -r "$hwmon" ]] || continue
        name=""
        { read -r name < "$hwmon"; } 2>/dev/null || continue
        if [[ "$name" == "coretemp" || "$name" == "k10temp" || "$name" == "zenpower" || "$name" == "cpu_thermal" ]]; then
            dir="${hwmon%/*}"
            if [[ -r "$dir/temp1_input" ]]; then
                printf '%s\n' "$dir/temp1_input"
                return 0
            fi
        fi
    done

    for tz in /sys/class/thermal/thermal_zone*/type; do
        [[ -r "$tz" ]] || continue
        type=""
        { read -r type < "$tz"; } 2>/dev/null || continue
        if [[ "$type" == *"x86_pkg_temp"* || "$type" == *"cpu"* ]]; then
            dir="${tz%/*}"
            if [[ -r "$dir/temp" ]]; then
                printf '%s\n' "$dir/temp"
                return 0
            fi
        fi
    done
    return 1
}

# ARCHITECTURAL NOTES & HARDWARE LIMITATIONS:
# 1. Intel Graphics Power (RAPL uncore):
#    On integrated Intel platforms, the energy counter for RAPL domain "uncore" is sampled
#    as an energy proxy for graphics / uncore activity. It does not measure discrete Intel Arc
#    cards or arbitrary discrete PCI devices, nor is it guaranteed 1:1 if multiple GPUs are present.
find_intel_rapl_uncore() {
    local name_file name_val
    for name_file in /sys/devices/virtual/powercap/intel-rapl/intel-rapl:0/*/name; do
        [[ -f "$name_file" ]] || continue
        name_val=$(cat "$name_file" 2>/dev/null || echo "")
        if [[ "$name_val" == "uncore" && -r "${name_file%/*}/energy_uj" ]]; then
            printf '%s\n' "${name_file%/*}/energy_uj"
            return 0
        fi
    done
    return 1
}

find_amd_pwr_sensor() {
    local card_node="$1" f
    for f in /sys/class/drm/"$card_node"/device/hwmon/hwmon*/power1_average /sys/class/drm/"$card_node"/device/hwmon/hwmon*/power1_input; do
        if [[ -f "$f" && -r "$f" ]]; then
            printf '%s\n' "$f"
            return 0
        fi
    done
    return 1
}

find_gpu_temp_sensors() {
    local card_node="$1" tfile
    for tfile in /sys/class/drm/"$card_node"/device/hwmon/hwmon*/temp*_input /sys/class/drm/"$card_node"/device/hwmon*/temp*_input; do
        [[ -f "$tfile" && -r "$tfile" ]] && printf '%s\n' "$tfile"
    done
}

find_disk_temp_sensors() {
    local dev_name="$1" ctrl_dev="" tfile real_p found=false
    local -A seen_paths=()

    for tfile in \
        /sys/class/block/"$dev_name"/device/hwmon*/temp*_input \
        /sys/class/block/"$dev_name"/device/hwmon/hwmon*/temp*_input; do
        if [[ -r "$tfile" ]]; then
            real_p=$(readlink -f -- "$tfile" 2>/dev/null) || real_p="$tfile"
            if [[ -z "${seen_paths[$real_p]:-}" ]]; then
                seen_paths["$real_p"]=1
                printf '%s\n' "$tfile"
                found=true
            fi
        fi
    done

    if [[ "$found" == false && "$dev_name" =~ ^(nvme[0-9]+) ]]; then
        ctrl_dev="${BASH_REMATCH[1]}"
        for tfile in \
            /sys/class/nvme/"$ctrl_dev"/hwmon*/temp*_input \
            /sys/class/nvme/"$ctrl_dev"/hwmon/hwmon*/temp*_input \
            /sys/class/nvme/"$ctrl_dev"/device/hwmon*/temp*_input \
            /sys/class/nvme/"$ctrl_dev"/device/hwmon/hwmon*/temp*_input; do
            if [[ -r "$tfile" ]]; then
                real_p=$(readlink -f -- "$tfile" 2>/dev/null) || real_p="$tfile"
                if [[ -z "${seen_paths[$real_p]:-}" ]]; then
                    seen_paths["$real_p"]=1
                    printf '%s\n' "$tfile"
                fi
            fi
        done
    fi
}

# 2. Intel GPU Usage (RC6 residency):
#    Non-residency in the RC6 low-power sleep state is an activity and power-state proxy,
#    not true multi-engine hardware compute utilization. The 50ms tolerance window mitigates
#    coarse-grained sleep and timer drift in user-space polling.
# 3. Intel Memory Accounting (fdinfo):
#    Aggregated client allocations across /proc/[0-9]*/fdinfo/* represent client-side
#    requested buffer objects (drm-total-system / drm-total-vram). Because buffers can be shared
#    or imported between clients (e.g. Wayland compositor and clients), this value is an
#    estimate of allocated driver memory, not a guaranteed hardware-physical VRAM footprint.

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
        tz_name="$2"
        place_lbl="$3"

        while true; do
            printf -v epoch '%(%s)T' -1
            printf -v local_offset '%(%z)T' "$epoch"

            if ! target_data=$(
                TZ="$tz_name" date -d "@$epoch" '+%I:%M:%S %p|%z' 2>/dev/null
            ); then
                send_osd "N/A"
                sleep 1
                continue
            fi

            time_str="${target_data%|*}"
            target_offset="${target_data##*|}"
            local_min=0 target_min=0

            if offset_to_minutes local_min "$local_offset" &&
               offset_to_minutes target_min "$target_offset"; then
                diff_min=$((target_min - local_min))

                if (( diff_min == 0 )); then
                    diff_lbl="same time"
                else
                    sign="+"
                    (( diff_min < 0 )) && sign="-"

                    absolute_diff=$diff_min
                    (( absolute_diff < 0 )) &&
                        absolute_diff=$((-absolute_diff))

                    diff_hours=$((absolute_diff / 60))
                    diff_mins=$((absolute_diff % 60))

                    if (( diff_mins == 0 )); then
                        diff_lbl="${sign}${diff_hours}h"
                    else
                        diff_lbl="${sign}${diff_hours}h ${diff_mins}m"
                    fi
                fi
                send_world_clock_osd "$time_str" "$place_lbl" "$diff_lbl"
            else
                send_world_clock_osd "$time_str" "$place_lbl" "N/A"
            fi
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
        DURATION_SEC="$2"
        TARGET_SEC=$((START_SEC + DURATION_SEC))

        while true; do
            left=$((TARGET_SEC - SECONDS))
            if (( left <= 0 )); then
                send_alert_notification "dusky-timer-alert" "󰔛  Time's Up!"
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
        WORK_SEC="$2"
        BREAK_SEC="$3"

        PHASE="WORK"
        TARGET_SEC=$((START_SEC + WORK_SEC))

        while true; do
            left=$((TARGET_SEC - SECONDS))

            if (( left <= 0 )); then
                if [[ "$PHASE" == "WORK" ]] && (( BREAK_SEC > 0 )); then
                    send_alert_notification "dusky-pomo-alert" "󰦖  Break Time!"
                    play_sound "/usr/share/sounds/gnome/default/alarms/glass-bell.oga"

                    PHASE="BREAK"
                    TARGET_SEC=$((SECONDS + BREAK_SEC))
                    continue
                else
                    msg="Session Finished"
                    (( BREAK_SEC > 0 )) && msg="Back to Work!"

                    send_alert_notification "dusky-pomo-alert" "󰔚  $msg"
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
        has_energy_range=false
        energy_range=0
        last_energy=0
        last_time_us=0
        has_baseline=false

        while true; do
            if [[ ! -r "$path" ]]; then
                has_baseline=false
                has_energy_range=false
                energy_range=0
                send_osd "N/A"
                sleep 3
                continue
            fi

            # Dynamically read hardware counter range if not already discovered
            if [[ "$has_energy_range" == false ]]; then
                if { read -r range_val < "${path%/*}/max_energy_range_uj"; } 2>/dev/null &&
                   [[ "$range_val" =~ ^[0-9]+$ ]] && (( range_val > 0 )); then
                    energy_range=$range_val
                    has_energy_range=true
                fi
            fi

            curr_time_us=0
            if { read -r current_energy < "$path"; } 2>/dev/null && monotonic_us curr_time_us; then
                if [[ "$has_baseline" == true ]]; then
                    delta_energy=$((current_energy - last_energy))
                    delta_time_us=$((curr_time_us - last_time_us))

                    if (( delta_energy < 0 )); then
                        if [[ "$has_energy_range" == true ]]; then
                            delta_energy=$((delta_energy + energy_range))
                        else
                            delta_energy=-1
                        fi
                    fi

                    if (( delta_time_us > 0 && delta_time_us <= 5000000 && delta_energy >= 0 )); then
                        watts_x10=$(( (delta_energy * 10) / delta_time_us ))
                        send_osd "$((watts_x10 / 10)).$((watts_x10 % 10))W"
                    else
                        send_osd "N/A"
                    fi
                else
                    send_osd "N/A"
                fi

                last_energy=$current_energy
                last_time_us=$curr_time_us
                has_baseline=true
            else
                has_baseline=false
                has_energy_range=false
                energy_range=0
                send_osd "N/A"
            fi
            sleep 1
        done
        ;;

    --cpu)
        prev_idle=0
        prev_total=0
        has_prev=false

        if { read -r _ user nice system idle iowait irq softirq steal _ < /proc/stat; } 2>/dev/null; then
            prev_idle=$((idle + iowait))
            prev_total=$((user + nice + system + idle + iowait + irq + softirq + steal))
            has_prev=true
        fi

        sleep 1

        while true; do
            if { read -r _ user nice system idle iowait irq softirq steal _ < /proc/stat; } 2>/dev/null; then
                idle_all=$((idle + iowait))
                total=$((user + nice + system + idle + iowait + irq + softirq + steal))

                if [[ "$has_prev" == true ]]; then
                    diff_idle=$((idle_all - prev_idle))
                    diff_total=$((total - prev_total))

                    if (( diff_total > 0 )); then
                        usage=$(( 100 * (diff_total - diff_idle) / diff_total ))
                        (( usage < 0 )) && usage=0
                        (( usage > 100 )) && usage=100
                        send_osd "${usage}%"
                    else
                        send_osd "N/A"
                    fi
                else
                    send_osd "N/A"
                fi

                prev_idle=$idle_all
                prev_total=$total
                has_prev=true
            else
                has_prev=false
                send_osd "N/A"
            fi
            sleep 1
        done
        ;;

    --ram)
        while true; do
            mem_tot=0; mem_avail=0
            has_tot=false; has_avail=false

            while read -r key val _; do
                case "$key" in
                    MemTotal:) mem_tot=$val; has_tot=true ;;
                    MemAvailable:) mem_avail=$val; has_avail=true ;;
                esac
                if [[ "$has_tot" == true && "$has_avail" == true ]]; then
                    break
                fi
            done < /proc/meminfo

            # MemAvailable=0 is valid under severe pressure; only fail if missing or malformed
            if [[ "$has_tot" == true && "$has_avail" == true ]] &&
               (( mem_tot > 0 && mem_avail >= 0 && mem_avail <= mem_tot )); then
                ram_mb=$(( (mem_tot - mem_avail) / 1024 ))
                send_osd "${ram_mb}"
            else
                send_osd "N/A"
            fi
            sleep 1
        done
        ;;

    --ram-temp)
        temp_files=()

        while true; do
            if (( ${#temp_files[@]} == 0 )); then
                for hwmon_dir in /sys/class/hwmon/hwmon*/; do
                    name_file="${hwmon_dir}name"
                    [[ -f "$name_file" ]] || continue
                    name=""
                    { read -r name < "$name_file"; } 2>/dev/null || continue
                    if [[ "$name" == "spd5118" || "$name" == "jc42" ]]; then
                        tfile="${hwmon_dir}temp1_input"
                        [[ -f "$tfile" ]] && temp_files+=("$tfile")
                    fi
                done
            fi

            if [[ ${#temp_files[@]} -gt 0 ]]; then
                temps=()
                for tf in "${temp_files[@]}"; do
                    if { read -r t < "$tf"; } 2>/dev/null; then
                        temps+=("$((t/1000))°")
                    fi
                done
                if [[ ${#temps[@]} -gt 0 ]]; then
                    send_osd "${temps[*]}"
                else
                    temp_files=()
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
            if [[ -f "$zram_file" ]] && { read -r orig_data compr_data mem_used _ _ _ _ _ _ < "$zram_file"; } 2>/dev/null; then
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
        last_zone_discover=-5

        while true; do
            if [[ -z "$zone_file" || ! -r "$zone_file" ]]; then
                if (( SECONDS - last_zone_discover >= 5 )); then
                    last_zone_discover=$SECONDS
                    zone_file=$(find_cpu_temp_sensor || echo "")
                fi
            fi

            if [[ -n "$zone_file" ]] && { read -r t < "$zone_file"; } 2>/dev/null; then
                temp_c=$(( t / 1000 ))
                send_osd "${temp_c}°C"
            else
                zone_file=""
                send_osd "N/A"
            fi
            sleep 1
        done
        ;;

    --battery|--battery-percent|--battery-watts|--battery-time)
        bat_dir=""

        while true; do
            # Verify battery presence; rediscover dynamically if missing, hotplugged, or unpresent
            is_valid_bat=false
            if [[ -n "$bat_dir" && -d "$bat_dir" ]]; then
                present_val=""
                { read -r present_val < "$bat_dir/present"; } 2>/dev/null || present_val="1"
                [[ "$present_val" != "0" ]] && is_valid_bat=true
            fi

            if [[ "$is_valid_bat" == false ]]; then
                bat_dir=$(find_system_battery || echo "")
            fi

            if [[ -n "$bat_dir" ]]; then
                if [[ "$MODE" == "--battery-percent" ]]; then
                    cap="?"
                    if { read -r cap < "$bat_dir/capacity"; } 2>/dev/null && [[ "$cap" =~ ^[0-9]+$ ]]; then
                        send_osd "${cap}%"
                    else
                        send_osd "Bat: N/A"
                    fi
                    sleep 1
                    continue
                fi

                cap="?"
                stat="Unknown"
                { read -r cap < "$bat_dir/capacity"; } 2>/dev/null || cap="?"
                { read -r stat < "$bat_dir/status"; } 2>/dev/null || stat="Unknown"

                # Read power or compute from voltage & current
                watts_str="N/A"
                pwr=0
                has_power=false

                if [[ -f "$bat_dir/power_now" ]]; then
                    raw_pwr=""
                    if { read -r raw_pwr < "$bat_dir/power_now"; } 2>/dev/null && [[ "$raw_pwr" =~ ^-?[0-9]+$ ]]; then
                        pwr=${raw_pwr#-}
                        watts_int=$(( pwr / 1000000 ))
                        watts_frac=$(( (pwr % 1000000) / 100000 ))
                        watts_str="${watts_int}.${watts_frac}W"
                        has_power=true
                    fi
                fi

                if [[ "$has_power" == false ]] && [[ -f "$bat_dir/current_now" && -f "$bat_dir/voltage_now" ]]; then
                    curr="" volt=""
                    if { read -r curr < "$bat_dir/current_now"; } 2>/dev/null &&
                       { read -r volt < "$bat_dir/voltage_now"; } 2>/dev/null &&
                       [[ "$curr" =~ ^-?[0-9]+$ && "$volt" =~ ^[0-9]+$ ]]; then
                        c_abs=${curr#-}
                        p_uw=$(( (c_abs / 1000) * (volt / 1000) ))
                        watts_int=$(( p_uw / 1000000 ))
                        watts_frac=$(( (p_uw % 1000000) / 100000 ))
                        watts_str="${watts_int}.${watts_frac}W"
                        pwr=$p_uw
                        has_power=true
                    fi
                fi

                if [[ "$MODE" == "--battery-watts" ]]; then
                    send_osd "$watts_str"
                    sleep 1
                    continue
                fi

                # Compute remaining time if requested (energy/power first, fallback to charge/current if energy failed)
                time_str=""
                if [[ "$stat" == "Discharging" ]]; then
                    if [[ "$has_power" == true && -f "$bat_dir/energy_now" ]] && (( pwr > 0 )); then
                        energy_now=""
                        if { read -r energy_now < "$bat_dir/energy_now"; } 2>/dev/null && [[ "$energy_now" =~ ^[0-9]+$ ]]; then
                            total_mins=$(( (energy_now * 60) / pwr ))
                            time_str=$'\n'"$(( total_mins / 60 ))h$(( total_mins % 60 ))m"
                        fi
                    fi
                    if [[ -z "$time_str" ]] && [[ -f "$bat_dir/charge_now" && -f "$bat_dir/current_now" ]]; then
                        charge_now="" curr_now=""
                        if { read -r charge_now < "$bat_dir/charge_now"; } 2>/dev/null &&
                           { read -r curr_now < "$bat_dir/current_now"; } 2>/dev/null &&
                           [[ "$charge_now" =~ ^[0-9]+$ && "$curr_now" =~ ^-?[0-9]+$ ]]; then
                            c_abs=${curr_now#-}
                            if (( c_abs > 0 )); then
                                total_mins=$(( (charge_now * 60) / c_abs ))
                                time_str=$'\n'"$(( total_mins / 60 ))h$(( total_mins % 60 ))m"
                            fi
                        fi
                    fi
                elif [[ "$stat" == "Charging" ]]; then
                    if [[ "$has_power" == true && -f "$bat_dir/energy_now" && -f "$bat_dir/energy_full" ]] && (( pwr > 0 )); then
                        energy_now="" energy_full=""
                        if { read -r energy_now < "$bat_dir/energy_now"; } 2>/dev/null &&
                           { read -r energy_full < "$bat_dir/energy_full"; } 2>/dev/null &&
                           [[ "$energy_now" =~ ^[0-9]+$ && "$energy_full" =~ ^[0-9]+$ ]] &&
                           (( energy_full > energy_now )); then
                            total_mins=$(( ((energy_full - energy_now) * 60) / pwr ))
                            time_str=$'\n'"$(( total_mins / 60 ))h$(( total_mins % 60 ))m"
                        fi
                    fi
                    if [[ -z "$time_str" ]] && [[ -f "$bat_dir/charge_now" && -f "$bat_dir/charge_full" && -f "$bat_dir/current_now" ]]; then
                        charge_now="" charge_full="" curr_now=""
                        if { read -r charge_now < "$bat_dir/charge_now"; } 2>/dev/null &&
                           { read -r charge_full < "$bat_dir/charge_full"; } 2>/dev/null &&
                           { read -r curr_now < "$bat_dir/current_now"; } 2>/dev/null &&
                           [[ "$charge_now" =~ ^[0-9]+$ && "$charge_full" =~ ^[0-9]+$ && "$curr_now" =~ ^-?[0-9]+$ ]]; then
                            c_abs=${curr_now#-}
                            if (( c_abs > 0 && charge_full > charge_now )); then
                                total_mins=$(( ((charge_full - charge_now) * 60) / c_abs ))
                                time_str=$'\n'"$(( total_mins / 60 ))h$(( total_mins % 60 ))m"
                            fi
                        fi
                    fi
                fi

                if [[ "$MODE" == "--battery-time" ]]; then
                    if [[ -n "$time_str" ]]; then
                        out_str="${time_str#$'\n'}"
                    else
                        out_str="N/A"
                    fi
                else
                    printf -v out_str "%s%% %s%s" "$cap" "$watts_str" "$time_str"
                fi
                send_osd "$out_str"
            else
                send_osd "Bat: N/A"
            fi
            sleep 1
        done
        ;;

    --disk)
        command -v df >/dev/null 2>&1 || die "Missing command: df"

        while true; do
            df_out=$(df -h --output=used,size,pcent / 2>/dev/null) || df_out=""
            if [[ -n "$df_out" ]]; then
                row=""
                while IFS= read -r line; do
                    [[ -n "$line" ]] && row="$line"
                done <<< "$df_out"
                read -r used size pcent <<< "$row"
                if [[ -n "${used:-}" && -n "${size:-}" && -n "${pcent:-}" ]]; then
                    send_osd "${used}/${size} ${pcent}"
                else
                    send_osd "Disk: N/A"
                fi
            else
                send_osd "Disk: N/A"
            fi
            sleep 1
        done
        ;;

    --disk-read|--disk-write)
        DEV="$2"
        stat_file="/sys/class/block/$DEV/stat"

        [[ -r "$stat_file" ]] ||
            die "Cannot read block-device statistics: $DEV"

        field=2
        [[ "$MODE" == "--disk-write" ]] && field=6

        have_previous=false

        while true; do
            if ! { read -r -a stats < "$stat_file"; } 2>/dev/null ||
               (( ${#stats[@]} <= field )) ||
               [[ ! "${stats[field]}" =~ ^[0-9]+$ ]]; then
                have_previous=false
                send_osd "N/A"
                sleep 1
                continue
            fi

            current_sectors="${stats[field]}"

            if ! monotonic_us current_time; then
                have_previous=false
                send_osd "N/A"
                sleep 1
                continue
            fi

            if [[ "$have_previous" == true ]]; then
                delta_sectors=$((current_sectors - previous_sectors))
                delta_us=$((current_time - previous_time))

                if (( delta_sectors >= 0 && delta_us > 0 )); then
                    rate_tenths=$((delta_sectors * 10000000 / (2048 * delta_us)))
                    total_mib=$((current_sectors / 2048))

                    send_osd \
                        "${total_mib} $((rate_tenths / 10)).$((rate_tenths % 10))"
                else
                    send_osd "N/A"
                fi
            else
                send_osd "Sampling"
            fi

            previous_sectors=$current_sectors
            previous_time=$current_time
            have_previous=true

            sleep 1
        done
        ;;

    --disk-temp)
        DEV="$2"
        mapfile -t temp_files < <(find_disk_temp_sensors "$DEV")

        has_smartctl=false
        if command -v smartctl >/dev/null 2>&1 && command -v jq >/dev/null 2>&1; then
            has_smartctl=true
        fi

        cached_smart_temp=""
        last_smart_query=-15
        last_temp_discover=$SECONDS
        (( ${#temp_files[@]} == 0 )) && last_temp_discover=-5

        while true; do
            # Periodically rediscover hwmon temperature paths if currently empty
            if (( ${#temp_files[@]} == 0 )) && (( SECONDS - last_temp_discover >= 5 )); then
                last_temp_discover=$SECONDS
                mapfile -t temp_files < <(find_disk_temp_sensors "$DEV")
            fi

            if [[ ${#temp_files[@]} -gt 0 ]]; then
                temps=()
                for tf in "${temp_files[@]}"; do
                    if { read -r t < "$tf"; } 2>/dev/null; then
                        temps+=("$((t/1000))°")
                    fi
                done
                if [[ ${#temps[@]} -gt 0 ]]; then
                    send_osd "${temps[*]}"
                else
                    temp_files=()
                    send_osd "N/A"
                fi
            elif [[ "$has_smartctl" == true ]]; then
                if (( SECONDS - last_smart_query >= 15 )); then
                    last_smart_query=$SECONDS
                    temp_candidate=""

                    smart_json=$(timeout --kill-after=1s 3s smartctl -Aj "/dev/$DEV" 2>/dev/null || true)
                    if [[ -n "$smart_json" ]]; then
                        temp_candidate=$(jq -er '.temperature.current | select(type == "number")' <<< "$smart_json" 2>/dev/null || echo "")
                    fi

                    # Fallback to sudo -n if unprivileged query failed or yielded no usable temperature (e.g. permission error JSON)
                    if [[ -z "$temp_candidate" ]]; then
                        smart_json=$(timeout --kill-after=1s 3s sudo -n smartctl -Aj "/dev/$DEV" 2>/dev/null || true)
                        if [[ -n "$smart_json" ]]; then
                            temp_candidate=$(jq -er '.temperature.current | select(type == "number")' <<< "$smart_json" 2>/dev/null || echo "")
                        fi
                    fi

                    cached_smart_temp="$temp_candidate"
                fi

                if [[ -n "$cached_smart_temp" ]]; then
                    send_osd "${cached_smart_temp}°"
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
        NET_STATE_DIR="${XDG_RUNTIME_DIR:-/run/user/$UID}/waybar-net"
        STATE_FILE="$NET_STATE_DIR/state"
        HEARTBEAT_FILE="$NET_STATE_DIR/heartbeat"
        DAEMON_PID_FILE="$NET_STATE_DIR/daemon.pid"

        if [[ -d "$NET_STATE_DIR" ]]; then
            : > "$HEARTBEAT_FILE" 2>/dev/null || true
            if [[ -r "$DAEMON_PID_FILE" ]]; then
                read -r d_pid < "$DAEMON_PID_FILE" 2>/dev/null || d_pid=""
                case "$d_pid" in
                    ""|*[!0-9]*) ;;
                    *)
                        if kill -0 "$d_pid" 2>/dev/null; then
                            _gfd=""
                            if { exec {_gfd}< "/proc/$d_pid/cmdline"; } 2>/dev/null; then
                                IFS= read -r -d '' _g1 <&"$_gfd" 2>/dev/null || _g1=""
                                IFS= read -r -d '' _g2 <&"$_gfd" 2>/dev/null || _g2=""
                                { exec {_gfd}<&-; } 2>/dev/null
                                [[ "$_g2" == *network_meter_daemon* ]] && kill -USR1 "$d_pid" 2>/dev/null || true
                            fi
                        fi
                        ;;
                esac
            fi
        fi

        while true; do
            [[ -d "$NET_STATE_DIR" ]] && : > "$HEARTBEAT_FILE" 2>/dev/null || true
            if [[ -r "$DAEMON_PID_FILE" ]]; then
                read -r _gp < "$DAEMON_PID_FILE" 2>/dev/null || _gp=""
                case "$_gp" in
                    ""|*[!0-9]*) ;;
                    *)
                        if kill -0 "$_gp" 2>/dev/null; then
                            _gfd=""
                            if { exec {_gfd}< "/proc/$_gp/cmdline"; } 2>/dev/null; then
                                IFS= read -r -d '' _g1 <&"$_gfd" 2>/dev/null || _g1=""
                                IFS= read -r -d '' _g2 <&"$_gfd" 2>/dev/null || _g2=""
                                { exec {_gfd}<&-; } 2>/dev/null
                                [[ "$_g2" == *network_meter_daemon* ]] && kill -USR1 "$_gp" 2>/dev/null || true
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
            if { read -r up_time _ < /proc/uptime; } 2>/dev/null; then
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
                has_energy_range=false
                energy_range=0
                last_energy=0
                last_time_us=0
                has_baseline=false

                while true; do
                    if [[ -z "$path" || ! -r "$path" ]]; then
                        path=$(find_intel_rapl_uncore || echo "")
                        has_baseline=false
                        has_energy_range=false
                        energy_range=0
                    fi

                    if [[ -z "$path" || ! -r "$path" ]]; then
                        has_baseline=false
                        has_energy_range=false
                        energy_range=0
                        send_osd "N/A"
                        sleep 3
                        continue
                    fi

                    if [[ "$has_energy_range" == false ]]; then
                        if { read -r range_val < "${path%/*}/max_energy_range_uj"; } 2>/dev/null &&
                           [[ "$range_val" =~ ^[0-9]+$ ]] && (( range_val > 0 )); then
                            energy_range=$range_val
                            has_energy_range=true
                        fi
                    fi

                    curr_time_us=0
                    if { read -r current_energy < "$path"; } 2>/dev/null && monotonic_us curr_time_us; then
                        if [[ "$has_baseline" == true ]]; then
                            delta_energy=$((current_energy - last_energy))
                            delta_time_us=$((curr_time_us - last_time_us))

                            if (( delta_energy < 0 )); then
                                if [[ "$has_energy_range" == true ]]; then
                                    delta_energy=$((delta_energy + energy_range))
                                else
                                    delta_energy=-1
                                fi
                            fi

                            if (( delta_time_us > 0 && delta_time_us <= 5000000 && delta_energy >= 0 )); then
                                watts_x10=$(( (delta_energy * 10) / delta_time_us ))
                                send_osd "$((watts_x10 / 10)).$((watts_x10 % 10))W"
                            else
                                send_osd "N/A"
                            fi
                        else
                            send_osd "N/A"
                        fi
                        last_energy=$current_energy
                        last_time_us=$curr_time_us
                        has_baseline=true
                    else
                        path=""
                        has_baseline=false
                        has_energy_range=false
                        energy_range=0
                        send_osd "N/A"
                    fi
                    sleep 1
                done
                ;;

            amd)
                path=""

                while true; do
                    if [[ -z "$path" || ! -r "$path" ]]; then
                        path=""
                        for f in /sys/class/drm/"$card"/device/hwmon/hwmon*/power1_average /sys/class/drm/"$card"/device/hwmon/hwmon*/power1_input; do
                            if [[ -f "$f" ]]; then
                                path="$f"
                                break
                            fi
                        done
                    fi

                    if [[ -n "$path" && -r "$path" ]] && { read -r microwatts < "$path"; } 2>/dev/null; then
                        watts_x10=$(( microwatts / 100000 ))
                        send_osd "$((watts_x10 / 10)).$((watts_x10 % 10))W"
                    else
                        path=""
                        send_osd "N/A"
                    fi
                    sleep 1
                done
                ;;

            nvidia)
                command -v nvidia-smi >/dev/null 2>&1 || die "Missing command: nvidia-smi"
                init_nvidia_device "$card" || die "Cannot identify NVIDIA device for $card"

                while true; do
                    if is_nvidia_suspended "$card"; then
                        send_osd "D3"
                    else
                        if ! power_str=$(query_nvidia power.draw); then
                            power_str=""
                        fi
                        power_str="${power_str//[[:space:]]/}"
                        if [[ "$power_str" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
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
                last_rc6=0
                last_time_us=0
                has_rc6_baseline=false

                while true; do
                    if [[ ! -r "$path" ]]; then
                        has_rc6_baseline=false
                        send_osd "N/A"
                        sleep 3
                        continue
                    fi

                    if { read -r current_rc6 < "$path"; } 2>/dev/null && monotonic_us curr_time_us; then
                        if [[ "$has_rc6_baseline" == true ]]; then
                            delta_rc6=$((current_rc6 - last_rc6))
                            delta_time_ms=$(( (curr_time_us - last_time_us) / 1000 ))

                            # Allow slight 50ms timestamping tolerance for near-zero activity
                            if (( delta_time_ms > 0 && delta_time_ms <= 5000 && delta_rc6 >= 0 && delta_rc6 <= delta_time_ms + 50 )); then
                                if (( delta_rc6 > delta_time_ms )); then
                                    usage=0
                                else
                                    usage=$(( 100 * (delta_time_ms - delta_rc6) / delta_time_ms ))
                                fi
                                (( usage < 0 )) && usage=0
                                (( usage > 100 )) && usage=100
                                send_osd "${usage}%"
                            else
                                send_osd "N/A"
                            fi
                        else
                            send_osd "N/A"
                        fi
                        last_rc6=$current_rc6
                        last_time_us=$curr_time_us
                        has_rc6_baseline=true
                    else
                        has_rc6_baseline=false
                        send_osd "N/A"
                    fi
                    sleep 1
                done
                ;;

            amd)
                path="/sys/class/drm/$card/device/gpu_busy_percent"

                while true; do
                    if [[ -r "$path" ]] && { read -r usage < "$path"; } 2>/dev/null; then
                        send_osd "${usage}%"
                    else
                        send_osd "N/A"
                    fi
                    sleep 1
                done
                ;;

            nvidia)
                command -v nvidia-smi >/dev/null 2>&1 || die "Missing command: nvidia-smi"
                init_nvidia_device "$card" || die "Cannot identify NVIDIA device for $card"

                while true; do
                    if is_nvidia_suspended "$card"; then
                        send_osd "D3"
                    else
                        if ! usage=$(query_nvidia utilization.gpu); then
                            usage=""
                        fi
                        usage="${usage//[[:space:]]/}"
                        if [[ "$usage" =~ ^[0-9]+$ ]]; then
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
                target_pdev=""
                if resolved=$(readlink -f -- "/sys/class/drm/$card/device" 2>/dev/null); then
                    target_pdev="${resolved##*/}"
                fi

                last_intel_mem_scan=-5
                cached_intel_mem="N/A"

                while true; do
                    # Scan memory allocations every 5 seconds to reduce proc fdinfo overhead
                    if (( SECONDS - last_intel_mem_scan >= 5 )); then
                        last_intel_mem_scan=$SECONDS
                        declare -A client_mem=()
                        found_accounting=false

                        if [[ -n "$target_pdev" ]]; then
                            for f in /proc/[0-9]*/fdinfo/*; do
                                [[ -f "$f" && -r "$f" ]] || continue
                                driver=""
                                client_id=""
                                pdev=""
                                total_sys=0
                                total_vram=0
                                has_mem_field=false

                                while read -r name val unit || [[ -n "$name" ]]; do
                                    case "$name" in
                                        drm-driver:) driver="$val" ;;
                                        drm-client-id:) client_id="$val" ;;
                                        drm-pdev:) pdev="$val" ;;
                                        drm-total-system0:|drm-total-system:)
                                            if [[ "$val" =~ ^[0-9]+$ ]]; then
                                                case "$unit" in
                                                    KiB) total_sys=$((val * 1024)); has_mem_field=true ;;
                                                    MiB) total_sys=$((val * 1048576)); has_mem_field=true ;;
                                                    GiB) total_sys=$((val * 1073741824)); has_mem_field=true ;;
                                                    B|"") total_sys=$val; has_mem_field=true ;;
                                                esac
                                            fi
                                            ;;
                                        drm-total-vram0:|drm-total-vram:)
                                            if [[ "$val" =~ ^[0-9]+$ ]]; then
                                                case "$unit" in
                                                    KiB) total_vram=$((val * 1024)); has_mem_field=true ;;
                                                    MiB) total_vram=$((val * 1048576)); has_mem_field=true ;;
                                                    GiB) total_vram=$((val * 1073741824)); has_mem_field=true ;;
                                                    B|"") total_vram=$val; has_mem_field=true ;;
                                                esac
                                            fi
                                            ;;
                                    esac
                                done < "$f" 2>/dev/null || true

                                # Filter strictly to the targeted PCI device and Intel driver with verified memory fields
                                if [[ "$driver" =~ ^(i915|xe)$ && -n "$client_id" && "$pdev" == "$target_pdev" && "$has_mem_field" == true ]]; then
                                    found_accounting=true
                                    total=$((total_sys + total_vram))
                                    key="${driver}_${pdev}_${client_id}"
                                    if [[ -z "${client_mem[$key]:-}" ]] || (( total > client_mem[$key] )); then
                                        client_mem["$key"]=$total
                                    fi
                                fi
                            done
                        fi

                        if [[ "$found_accounting" == true ]]; then
                            sum=0
                            for key in "${!client_mem[@]}"; do
                                sum=$((sum + client_mem[$key]))
                            done
                            cached_intel_mem="$((sum / 1048576))MB"
                        else
                            cached_intel_mem="N/A"
                        fi
                    fi

                    send_osd "$cached_intel_mem"
                    sleep 1
                done
                ;;

            amd)
                used_path="/sys/class/drm/$card/device/mem_info_vram_used"

                while true; do
                    if [[ -r "$used_path" ]] && { read -r used < "$used_path"; } 2>/dev/null; then
                        send_osd "$(( used / 1048576 ))MB"
                    else
                        send_osd "N/A"
                    fi
                    sleep 1
                done
                ;;

            nvidia)
                command -v nvidia-smi >/dev/null 2>&1 || die "Missing command: nvidia-smi"
                init_nvidia_device "$card" || die "Cannot identify NVIDIA device for $card"

                while true; do
                    if is_nvidia_suspended "$card"; then
                        send_osd "D3"
                    else
                        if ! used=$(query_nvidia memory.used); then
                            used=""
                        fi
                        used="${used//[[:space:]]/}"
                        if [[ "$used" =~ ^[0-9]+$ ]]; then
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

        cpu_zone_file=$(find_cpu_temp_sensor || echo "")
        last_cpu_temp_discover=$SECONDS
        [[ -z "$cpu_zone_file" ]] && last_cpu_temp_discover=-5

        cpu_energy_path="/sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj"
        has_cpu_power=false
        has_cpu_range=false
        cpu_energy_range=0
        last_cpu_pwr_discover=$SECONDS
        if [[ -r "$cpu_energy_path" ]]; then
            has_cpu_power=true
            if { read -r crange < "${cpu_energy_path%/*}/max_energy_range_uj"; } 2>/dev/null &&
               [[ "$crange" =~ ^[0-9]+$ ]] && (( crange > 0 )); then
                cpu_energy_range=$crange
                has_cpu_range=true
            fi
        else
            last_cpu_pwr_discover=-5
        fi

        NVIDIA_PCI_ID=""
        if [[ "${vendor,,}" == "nvidia" ]]; then
            init_nvidia_device "$card" 2>/dev/null || true
        fi

        amd_pwr_path=""
        last_amd_pwr_discover=-5
        if [[ "${vendor,,}" == "amd" ]]; then
            amd_pwr_path=$(find_amd_pwr_sensor "$card" || echo "")
            [[ -n "$amd_pwr_path" ]] && last_amd_pwr_discover=$SECONDS
        fi

        intel_target_pdev=""
        if [[ "${vendor,,}" == "intel" ]] && resolved=$(readlink -f -- "/sys/class/drm/$card/device" 2>/dev/null); then
            intel_target_pdev="${resolved##*/}"
        fi

        intel_pwr_path=""
        has_intel_pwr_range=false
        intel_energy_range=0
        last_intel_pwr_discover=-5
        if [[ "${vendor,,}" == "intel" ]]; then
            intel_pwr_path=$(find_intel_rapl_uncore || echo "")
            if [[ -n "$intel_pwr_path" ]]; then
                last_intel_pwr_discover=$SECONDS
                if { read -r irange < "${intel_pwr_path%/*}/max_energy_range_uj"; } 2>/dev/null &&
                   [[ "$irange" =~ ^[0-9]+$ ]] && (( irange > 0 )); then
                    intel_energy_range=$irange
                    has_intel_pwr_range=true
                fi
            fi
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
        last_gpu_temp_discover=-5
        if [[ -n "$card" ]]; then
            mapfile -t gpu_temp_files < <(find_gpu_temp_sensors "$card")
            (( ${#gpu_temp_files[@]} > 0 )) && last_gpu_temp_discover=$SECONDS
        fi

        prev_idle=0
        prev_total=0
        has_cpu_baseline=false
        if { read -r _ user nice system idle iowait irq softirq steal _ < /proc/stat; } 2>/dev/null; then
            prev_idle=$((idle + iowait))
            prev_total=$((user + nice + system + idle + iowait + irq + softirq + steal))
            has_cpu_baseline=true
        fi

        last_cpu_energy=0
        last_cpu_time=0
        has_cpu_energy_baseline=false
        if [[ "$has_cpu_power" == true ]] && { read -r last_cpu_energy < "$cpu_energy_path"; } 2>/dev/null && monotonic_us last_cpu_time; then
            has_cpu_energy_baseline=true
        fi

        last_gpu_energy=0
        last_gpu_time=0
        has_gpu_energy_baseline=false
        if [[ -n "$intel_pwr_path" && -r "$intel_pwr_path" ]] && { read -r last_gpu_energy < "$intel_pwr_path"; } 2>/dev/null && monotonic_us last_gpu_time; then
            has_gpu_energy_baseline=true
        fi

        last_rc6=0
        last_rc6_time=0
        has_rc6_baseline=false
        if [[ -n "$intel_rc6_path" && -r "$intel_rc6_path" ]] && { read -r last_rc6 < "$intel_rc6_path"; } 2>/dev/null && monotonic_us last_rc6_time; then
            has_rc6_baseline=true
        fi

        last_hud_mem_scan=-5
        cached_hud_vram="N/A"

        sleep 1

        while true; do
            cpu_usage="N/A"
            if { read -r _ user nice system idle iowait irq softirq steal _ < /proc/stat; } 2>/dev/null; then
                idle_all=$((idle + iowait))
                total=$((user + nice + system + idle + iowait + irq + softirq + steal))

                if [[ "$has_cpu_baseline" == true ]]; then
                    diff_idle=$((idle_all - prev_idle))
                    diff_total=$((total - prev_total))

                    if (( diff_total > 0 )); then
                        u=$(( 100 * (diff_total - diff_idle) / diff_total ))
                        (( u < 0 )) && u=0
                        (( u > 100 )) && u=100
                        cpu_usage="${u}%"
                    fi
                fi
                prev_idle=$idle_all
                prev_total=$total
                has_cpu_baseline=true
            else
                has_cpu_baseline=false
            fi

            # CPU power with recovery
            if [[ "$has_cpu_power" == false ]]; then
                if (( SECONDS - last_cpu_pwr_discover >= 5 )); then
                    last_cpu_pwr_discover=$SECONDS
                    if [[ -r "$cpu_energy_path" ]]; then
                        has_cpu_power=true
                        has_cpu_energy_baseline=false
                        if { read -r crange < "${cpu_energy_path%/*}/max_energy_range_uj"; } 2>/dev/null &&
                           [[ "$crange" =~ ^[0-9]+$ ]] && (( crange > 0 )); then
                            cpu_energy_range=$crange
                            has_cpu_range=true
                        else
                            has_cpu_range=false
                        fi
                    fi
                fi
            fi

            cpu_watts="N/A"
            if [[ "$has_cpu_power" == true ]]; then
                curr_cpu_time=0
                if { read -r current_cpu_energy < "$cpu_energy_path"; } 2>/dev/null && monotonic_us curr_cpu_time; then
                    if [[ "$has_cpu_energy_baseline" == true ]]; then
                        delta_energy=$((current_cpu_energy - last_cpu_energy))
                        delta_time_us=$((curr_cpu_time - last_cpu_time))

                        if (( delta_energy < 0 )); then
                            if [[ "$has_cpu_range" == true ]]; then
                                delta_energy=$(( delta_energy + cpu_energy_range ))
                            else
                                delta_energy=-1
                            fi
                        fi

                        if (( delta_time_us > 0 && delta_time_us <= 5000000 && delta_energy >= 0 )); then
                            watts_x10=$(( (delta_energy * 10) / delta_time_us ))
                            cpu_watts="$(( watts_x10 / 10 )).$(( watts_x10 % 10 ))W"
                        fi
                    fi
                    last_cpu_energy=$current_cpu_energy
                    last_cpu_time=$curr_cpu_time
                    has_cpu_energy_baseline=true
                else
                    has_cpu_power=false
                    has_cpu_energy_baseline=false
                fi
            fi

            # CPU temperature with recovery
            if [[ -z "$cpu_zone_file" || ! -r "$cpu_zone_file" ]]; then
                if (( SECONDS - last_cpu_temp_discover >= 5 )); then
                    last_cpu_temp_discover=$SECONDS
                    cpu_zone_file=$(find_cpu_temp_sensor || echo "")
                fi
            fi

            cpu_temp="N/A"
            if [[ -n "$cpu_zone_file" ]] && { read -r t < "$cpu_zone_file"; } 2>/dev/null; then
                cpu_temp="$(( t / 1000 ))°C"
            else
                cpu_zone_file=""
            fi

            mem_tot=0; mem_avail=0
            has_tot=false; has_avail=false
            while read -r key val _; do
                case "$key" in
                    MemTotal:) mem_tot=$val; has_tot=true ;;
                    MemAvailable:) mem_avail=$val; has_avail=true ;;
                esac
                if [[ "$has_tot" == true && "$has_avail" == true ]]; then
                    break
                fi
            done < /proc/meminfo

            if [[ "$has_tot" == true && "$has_avail" == true ]] &&
               (( mem_tot > 0 && mem_avail >= 0 && mem_avail <= mem_tot )); then
                ram_used_mb=$(( (mem_tot - mem_avail) / 1024 ))
                ram_used_gb=$(( ram_used_mb / 1024 ))
                ram_used_gb_frac=$(( (ram_used_mb % 1024) * 10 / 1024 ))
                ram_str="${ram_used_gb}.${ram_used_gb_frac}GB"
            else
                ram_str="N/A"
            fi

            gpu_usage="N/A"
            gpu_watts="N/A"
            gpu_vram="N/A"
            gpu_temp="N/A"
            vram_label="VRAM"

            if [[ -n "$card" ]] && (( ${#gpu_temp_files[@]} == 0 )) && (( SECONDS - last_gpu_temp_discover >= 5 )); then
                last_gpu_temp_discover=$SECONDS
                mapfile -t gpu_temp_files < <(find_gpu_temp_sensors "$card")
            fi

            case "${vendor,,}" in
                intel)
                    vram_label="VRAM"

                    if [[ ${#gpu_temp_files[@]} -gt 0 ]] && { read -r gt < "${gpu_temp_files[0]}"; } 2>/dev/null; then
                        gpu_temp="$((gt/1000))°C"
                    else
                        gpu_temp_files=()
                    fi

                    if [[ -z "$intel_pwr_path" || ! -r "$intel_pwr_path" ]]; then
                        if (( SECONDS - last_intel_pwr_discover >= 5 )); then
                            last_intel_pwr_discover=$SECONDS
                            intel_pwr_path=$(find_intel_rapl_uncore || echo "")
                            has_gpu_energy_baseline=false
                            if [[ -n "$intel_pwr_path" ]] && { read -r irange < "${intel_pwr_path%/*}/max_energy_range_uj"; } 2>/dev/null &&
                               [[ "$irange" =~ ^[0-9]+$ ]] && (( irange > 0 )); then
                                intel_energy_range=$irange
                                has_intel_pwr_range=true
                            else
                                has_intel_pwr_range=false
                            fi
                        fi
                    fi

                    if [[ -n "$intel_pwr_path" && -r "$intel_pwr_path" ]]; then
                        curr_gpu_time=0
                        if { read -r current_gpu_energy < "$intel_pwr_path"; } 2>/dev/null && monotonic_us curr_gpu_time; then
                            if [[ "$has_gpu_energy_baseline" == true ]]; then
                                delta_energy=$((current_gpu_energy - last_gpu_energy))
                                delta_time_us=$((curr_gpu_time - last_gpu_time))

                                if (( delta_energy < 0 )); then
                                    if [[ "$has_intel_pwr_range" == true ]]; then
                                        delta_energy=$(( delta_energy + intel_energy_range ))
                                    else
                                        delta_energy=-1
                                    fi
                                fi

                                if (( delta_time_us > 0 && delta_time_us <= 5000000 && delta_energy >= 0 )); then
                                    watts_x10=$(( (delta_energy * 10) / delta_time_us ))
                                    gpu_watts="$(( watts_x10 / 10 )).$(( watts_x10 % 10 ))W"
                                fi
                            fi
                            last_gpu_energy=$current_gpu_energy
                            last_gpu_time=$curr_gpu_time
                            has_gpu_energy_baseline=true
                        else
                            intel_pwr_path=""
                            has_gpu_energy_baseline=false
                        fi
                    fi

                    if [[ -n "$intel_rc6_path" && -r "$intel_rc6_path" ]]; then
                        curr_rc6_time=0
                        if { read -r current_rc6 < "$intel_rc6_path"; } 2>/dev/null && monotonic_us curr_rc6_time; then
                            if [[ "$has_rc6_baseline" == true ]]; then
                                delta_rc6=$((current_rc6 - last_rc6))
                                delta_time_ms=$(( (curr_rc6_time - last_rc6_time) / 1000 ))

                                if (( delta_time_ms > 0 && delta_time_ms <= 5000 && delta_rc6 >= 0 && delta_rc6 <= delta_time_ms + 50 )); then
                                    if (( delta_rc6 > delta_time_ms )); then
                                        u=0
                                    else
                                        u=$(( 100 * (delta_time_ms - delta_rc6) / delta_time_ms ))
                                    fi
                                    (( u < 0 )) && u=0
                                    (( u > 100 )) && u=100
                                    gpu_usage="${u}%"
                                fi
                            fi
                            last_rc6=$current_rc6
                            last_rc6_time=$curr_rc6_time
                            has_rc6_baseline=true
                        else
                            has_rc6_baseline=false
                        fi
                    fi

                    # Cache Intel GPU allocation scan for 5 seconds to reduce HUD scan cost
                    if (( SECONDS - last_hud_mem_scan >= 5 )); then
                        last_hud_mem_scan=$SECONDS
                        declare -A client_mem=()
                        found_acc=false

                        if [[ -n "$intel_target_pdev" ]]; then
                            for f in /proc/[0-9]*/fdinfo/*; do
                                [[ -f "$f" && -r "$f" ]] || continue
                                driver=""
                                client_id=""
                                pdev=""
                                total_sys=0
                                total_vram=0
                                has_mem_field=false

                                while read -r name val unit || [[ -n "$name" ]]; do
                                    case "$name" in
                                        drm-driver:) driver="$val" ;;
                                        drm-client-id:) client_id="$val" ;;
                                        drm-pdev:) pdev="$val" ;;
                                        drm-total-system0:|drm-total-system:)
                                            if [[ "$val" =~ ^[0-9]+$ ]]; then
                                                case "$unit" in
                                                    KiB) total_sys=$((val * 1024)); has_mem_field=true ;;
                                                    MiB) total_sys=$((val * 1048576)); has_mem_field=true ;;
                                                    GiB) total_sys=$((val * 1073741824)); has_mem_field=true ;;
                                                    B|"") total_sys=$val; has_mem_field=true ;;
                                                esac
                                            fi
                                            ;;
                                        drm-total-vram0:|drm-total-vram:)
                                            if [[ "$val" =~ ^[0-9]+$ ]]; then
                                                case "$unit" in
                                                    KiB) total_vram=$((val * 1024)); has_mem_field=true ;;
                                                    MiB) total_vram=$((val * 1048576)); has_mem_field=true ;;
                                                    GiB) total_vram=$((val * 1073741824)); has_mem_field=true ;;
                                                    B|"") total_vram=$val; has_mem_field=true ;;
                                                esac
                                            fi
                                            ;;
                                    esac
                                done < "$f" 2>/dev/null || true

                                # Filter strictly to targeted PCI device and Intel driver with verified memory fields
                                if [[ "$driver" =~ ^(i915|xe)$ && -n "$client_id" && "$pdev" == "$intel_target_pdev" && "$has_mem_field" == true ]]; then
                                    found_acc=true
                                    total=$((total_sys + total_vram))
                                    key="${driver}_${pdev}_${client_id}"
                                    if [[ -z "${client_mem[$key]:-}" ]] || (( total > client_mem[$key] )); then
                                        client_mem["$key"]=$total
                                    fi
                                fi
                            done
                        fi

                        if [[ "$found_acc" == true ]]; then
                            sum=0
                            for key in "${!client_mem[@]}"; do
                                sum=$((sum + client_mem[$key]))
                            done || true
                            vram_mb=$((sum / 1048576))
                            vram_gb=$(( vram_mb / 1024 ))
                            vram_gb_frac=$(( (vram_mb % 1024) * 10 / 1024 ))
                            cached_hud_vram="${vram_gb}.${vram_gb_frac}GB"
                        else
                            cached_hud_vram="N/A"
                        fi
                    fi
                    gpu_vram="$cached_hud_vram"
                    ;;

                amd)
                    if [[ ${#gpu_temp_files[@]} -gt 0 ]] && { read -r gt < "${gpu_temp_files[0]}"; } 2>/dev/null; then
                        gpu_temp="$((gt/1000))°C"
                    else
                        gpu_temp_files=()
                    fi

                    if [[ -z "$amd_pwr_path" || ! -r "$amd_pwr_path" ]]; then
                        if (( SECONDS - last_amd_pwr_discover >= 5 )); then
                            last_amd_pwr_discover=$SECONDS
                            amd_pwr_path=$(find_amd_pwr_sensor "$card" || echo "")
                        fi
                    fi

                    if [[ -n "$amd_pwr_path" && -r "$amd_pwr_path" ]]; then
                        if { read -r microwatts < "$amd_pwr_path"; } 2>/dev/null; then
                            watts_x10=$(( microwatts / 100000 ))
                            gpu_watts="$(( watts_x10 / 10 )).$(( watts_x10 % 10 ))W"
                        else
                            amd_pwr_path=""
                        fi
                    fi

                    if [[ -n "$amd_busy_path" && -r "$amd_busy_path" ]]; then
                        if { read -r u < "$amd_busy_path"; } 2>/dev/null; then
                            gpu_usage="${u}%"
                        fi
                    fi

                    if [[ -n "$amd_vram_path" && -r "$amd_vram_path" ]]; then
                        if { read -r vram_bytes < "$amd_vram_path"; } 2>/dev/null; then
                            vram_mb=$(( vram_bytes / 1048576 ))
                            vram_gb=$(( vram_mb / 1024 ))
                            vram_gb_frac=$(( (vram_mb % 1024) * 10 / 1024 ))
                            gpu_vram="${vram_gb}.${vram_gb_frac}GB"
                        fi
                    fi
                    ;;

                nvidia)
                    # Check suspension FIRST before touching any sysfs hwmon nodes or nvidia-smi
                    if is_nvidia_suspended "$card"; then
                        gpu_usage="D3"
                        gpu_watts="D3"
                        gpu_vram="D3"
                        gpu_temp="D3"
                    elif [[ -n "$NVIDIA_PCI_ID" ]] && command -v nvidia-smi >/dev/null 2>&1; then
                        if [[ ${#gpu_temp_files[@]} -gt 0 ]] && { read -r gt < "${gpu_temp_files[0]}"; } 2>/dev/null; then
                            gpu_temp="$((gt/1000))°C"
                        else
                            gpu_temp_files=()
                        fi

                        if ! nv_info=$(query_nvidia power.draw,utilization.gpu,memory.used,temperature.gpu); then
                            nv_info=""
                        fi
                        if [[ -n "$nv_info" ]]; then
                            IFS=',' read -r nv_pwr nv_usg nv_mem_used nv_tmp <<< "$nv_info"
                            nv_pwr="${nv_pwr//[[:space:]]/}"
                            nv_usg="${nv_usg//[[:space:]]/}"
                            nv_mem_used="${nv_mem_used//[[:space:]]/}"
                            nv_tmp="${nv_tmp//[[:space:]]/}"

                            if [[ "$nv_pwr" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
                                if [[ "$nv_pwr" =~ ^([0-9]+)\.([0-9]) ]]; then
                                    gpu_watts="${BASH_REMATCH[1]}.${BASH_REMATCH[2]}W"
                                else
                                    gpu_watts="${nv_pwr}W"
                                fi
                            fi

                            [[ "$nv_usg" =~ ^[0-9]+$ ]] && gpu_usage="${nv_usg}%"

                            if [[ "$nv_mem_used" =~ ^[0-9]+$ ]]; then
                                nv_used_gb=$(( nv_mem_used / 1024 ))
                                nv_used_gb_frac=$(( (nv_mem_used % 1024) * 10 / 1024 ))
                                gpu_vram="${nv_used_gb}.${nv_used_gb_frac}GB"
                            fi

                            [[ "$nv_tmp" =~ ^[0-9]+$ ]] && gpu_temp="${nv_tmp}°C"
                        fi
                    fi
                    ;;
            esac

            printf -v hud_body '  %s • %s • %s\n  %s • %s • %s\n  %s | %s %s' \
                "$cpu_usage" "$cpu_watts" "$cpu_temp" \
                "$gpu_usage" "$gpu_watts" "$gpu_temp" \
                "$ram_str" "$vram_label" "$gpu_vram"

            send_hud_osd "$hud_body"
            sleep 1
        done
        ;;

    --workspace)
        command -v hyprctl >/dev/null 2>&1 || die "Missing command: hyprctl"

        while true; do
            ws_id="?"
            if ws_info=$(hyprctl -j activeworkspace 2>/dev/null); then
                if [[ "$ws_info" =~ \"id\":[[:space:]]*([0-9-]+) ]]; then
                    ws_id="${BASH_REMATCH[1]}"
                fi
            elif ws_info=$(hyprctl activeworkspace 2>/dev/null); then
                if [[ "$ws_info" =~ workspace\ ID\ ([0-9-]+) ]]; then
                    ws_id="${BASH_REMATCH[1]}"
                fi
            fi
            send_osd "WS: $ws_id"
            sleep 1
        done
        ;;
esac
