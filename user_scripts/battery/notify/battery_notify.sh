#!/usr/bin/env bash
#
# battery_notify.sh — Hyprland-only Event-Driven Battery Monitor (Fixed)
# Arch bleeding-edge, Hyprland 0.55.4+ (July 2026), systemd 261+, bash 5.2+, upower 1.90+
# No backwards compat, no UWSM. Part of hyprland-session.target
#
set -uo pipefail
export LC_NUMERIC=C

##########################
# CONFIGURATION — EDIT ME
##########################
readonly BATTERY_DEVICE="${BATTERY_DEVICE:-}"
readonly BATTERY_FULL_THRESHOLD="${BATTERY_FULL_THRESHOLD:-100}"
readonly BATTERY_LOW_THRESHOLD="${BATTERY_LOW_THRESHOLD:-20}"
readonly BATTERY_CRITICAL_THRESHOLD="${BATTERY_CRITICAL_THRESHOLD:-10}"
readonly BATTERY_UNPLUG_THRESHOLD="${BATTERY_UNPLUG_THRESHOLD:-100}"

readonly REPEAT_FULL_MIN="${REPEAT_FULL_MIN:-999}"
readonly REPEAT_LOW_MIN="${REPEAT_LOW_MIN:-3}"
readonly REPEAT_CRITICAL_MIN="${REPEAT_CRITICAL_MIN:-1}"
readonly SUSPEND_GRACE_SEC="${SUSPEND_GRACE_SEC:-60}"
readonly SAFETY_POLL_INTERVAL="${SAFETY_POLL_INTERVAL:-60}"
readonly DO_SUSPEND="${DO_SUSPEND:-true}"

readonly MSG_CRITICAL="${MSG_CRITICAL:-Suspending system!}"
readonly SOUND_LOW="${SOUND_LOW:-/usr/share/sounds/freedesktop/stereo/complete.oga}"
readonly SOUND_CRITICAL="${SOUND_CRITICAL:-/usr/share/sounds/freedesktop/stereo/suspend-error.oga}"
readonly SOUND_PLUG="${SOUND_PLUG:-/usr/share/sounds/freedesktop/stereo/device-added.oga}"
readonly SOUND_UNPLUG="${SOUND_UNPLUG:-/usr/share/sounds/freedesktop/stereo/device-removed.oga}"

readonly MAX_RETRIES=5

declare -g RUNNING=true
declare -g MON_FD=-1
declare -g UPMON_PID=""
declare -g CURRENT_MODE=""

declare -g HAS_NOTIFY=false
declare -g HAS_PAPLAY=false
declare -g HAS_PWPLAY=false

declare -g STATE_LAST=""
declare -g STATE_LAST_PERCENTAGE=999
declare -g STATE_LAST_FULL_NOTIFY=0
declare -g STATE_LAST_LOW_NOTIFY=0
declare -g STATE_LAST_CRITICAL_NOTIFY=0
declare -g STATE_LAST_SUSPEND_MONO=0

log() { printf '[%(%Y-%m-%d %H:%M:%S)T] [battery_notify] %s\n' -1 "$*" >&2; }
die() { log "FATAL: $*"; exit 1; }
is_integer() { [[ ${1:-} =~ ^[0-9]+$ ]]; }
get_wall_now() { printf '%s' "$EPOCHSECONDS"; }
get_mono_now() { local up; if up=$(awk '{print int($1)}' /proc/uptime 2>/dev/null) && is_integer "$up"; then printf '%s' "$up"; else printf '%s' "$SECONDS"; fi; }
get_icon() {
    local perc="${1:-0}" state="${2:-Discharging}"; perc="${perc%%.*}"; is_integer "$perc" || perc=0
    (( perc < 0 )) && perc=0; (( perc > 100 )) && perc=100
    if [[ "$state" == "Charging" ]]; then
        if (( perc <= 10 )); then printf 'battery-empty-charging'
        elif (( perc <= 20 )); then printf 'battery-caution-charging'
        elif (( perc <= 40 )); then printf 'battery-low-charging'
        elif (( perc <= 80 )); then printf 'battery-good-charging'
        else printf 'battery-full-charging'; fi
    else
        if (( perc <= 10 )); then printf 'battery-empty'
        elif (( perc <= 20 )); then printf 'battery-caution'
        elif (( perc <= 40 )); then printf 'battery-low'
        elif (( perc <= 80 )); then printf 'battery-good'
        else printf 'battery-full'; fi
    fi
}
play_sound() {
    local sound="${1:-}"; [[ -z "$sound" || "$sound" == "disabled" ]] && return 0; [[ -r "$sound" ]] || return 0
    if [[ "$HAS_PAPLAY" == "true" ]]; then paplay "$sound" >/dev/null 2>&1 & disown || true
    elif [[ "$HAS_PWPLAY" == "true" ]]; then pw-play "$sound" >/dev/null 2>&1 & disown || true; fi
}
fn_notify() {
    local urgency="$1" title="$2" body="$3" icon="$4" sound="$5"
    local runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$UID}" bus_path="$runtime_dir/bus"
    if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" && -S "$bus_path" ]]; then export DBUS_SESSION_BUS_ADDRESS="unix:path=$bus_path"; fi
    if [[ "$HAS_NOTIFY" == "true" ]]; then
        local err
        if [[ "$urgency" == "critical" ]]; then err=$(notify-send -a "Battery Monitor" -u "$urgency" -i "$icon" -- "$title" "$body" 2>&1) || log "notify-send failed: $err"
        else err=$(notify-send -a "Battery Monitor" -u "$urgency" -t 5000 -i "$icon" -- "$title" "$body" 2>&1) || log "notify-send failed: $err"; fi
    else log "Notification: [$urgency] $title - $body"; fi
    play_sound "$sound"
}
parse_upower_block() {
    local info="$1" state="" perc="" energy="" energy_full=""
    state=$(grep -i -m1 '^[[:space:]]*state:' <<< "$info" | cut -d: -f2- | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    perc=$(grep -i -m1 '^[[:space:]]*percentage:' <<< "$info" | grep -oE '[0-9]+(\.[0-9]+)?' | head -n1)
    energy=$(grep -i -m1 '^[[:space:]]*energy:[[:space:]]' <<< "$info" | grep -oE '[0-9]+(\.[0-9]+)?' | head -n1)
    energy_full=$(grep -i -m1 '^[[:space:]]*energy-full:' <<< "$info" | grep -oE '[0-9]+(\.[0-9]+)?' | head -n1)
    [[ -z "$state" ]] && return 1
    printf '%s|%s|%s|%s' "$state" "${perc:-}" "${energy:-}" "${energy_full:-}"
}
normalize_state() {
    local s="${1,,}"; s=$(echo "$s" | xargs)
    case "$s" in
        discharging) echo "Discharging" ;;
        not\ charging|not-charging) echo "Charging" ;;
        charging|pending\ charge|pending-charge) echo "Charging" ;;
        fully\ charged|fully-charged|full) echo "Full" ;;
        empty) echo "Empty" ;;
        *) echo "Unknown" ;;
    esac
}
read_battery_aggregated() {
    local dev info parsed state perc energy e_full
    if [[ -n "$BATTERY_DEVICE" ]]; then
        info=$(upower -i "$BATTERY_DEVICE" 2>/dev/null) || return 1
        parsed=$(parse_upower_block "$info") || return 1
        IFS='|' read -r state perc energy e_full <<< "$parsed"
        state=$(normalize_state "$state"); perc="${perc%%.*}"; is_integer "$perc" || return 1
        printf '%s;%s;%s' "$state" "$perc" "device:$BATTERY_DEVICE"; return 0
    fi
    local dd="/org/freedesktop/UPower/devices/DisplayDevice"
    info=$(upower -i "$dd" 2>/dev/null)
    if [[ -n "$info" ]]; then
        parsed=$(parse_upower_block "$info") || true
        if [[ -n "$parsed" ]]; then
            IFS='|' read -r state perc energy e_full <<< "$parsed"
            if [[ -n "$perc" ]]; then
                state=$(normalize_state "$state")
                if [[ "$state" != "Unknown" ]]; then
                    perc="${perc%%.*}"; is_integer "$perc" || perc=0
                    printf '%s;%s;%s' "$state" "$perc" "DisplayDevice"; return 0
                fi
            fi
        fi
    fi
    local -a devices; mapfile -t devices < <(upower -e 2>/dev/null | grep -i 'battery\|BAT' | grep -v -i 'hidpp\|keyboard\|mouse\|headset' || true)
    (( ${#devices[@]} == 0 )) && mapfile -t devices < <(upower -e 2>/dev/null)
    local total_energy=0 total_energy_full=0 sum_perc=0 count=0 any_charging=false any_discharging=false any_full=false any_empty=false
    for dev in "${devices[@]}"; do
        info=$(upower -i "$dev" 2>/dev/null) || continue
        grep -qi 'power supply:[[:space:]]*yes' <<< "$info" || continue
        parsed=$(parse_upower_block "$info") || continue
        IFS='|' read -r state perc energy e_full <<< "$parsed"; [[ -z "$perc" ]] && continue
        state=$(normalize_state "$state")
        case "$state" in Charging) any_charging=true;; Discharging) any_discharging=true;; Full) any_full=true;; Empty) any_empty=true; any_discharging=true;; esac
        sum_perc=$(awk -v a="$sum_perc" -v b="$perc" 'BEGIN{print a+b}'); ((count++))
        if [[ -n "$energy" && -n "$e_full" ]]; then total_energy=$(awk -v a="$total_energy" -v b="$energy" 'BEGIN{print a+b}'); total_energy_full=$(awk -v a="$total_energy_full" -v b="$e_full" 'BEGIN{print a+b}'); fi
    done
    (( count == 0 )) && return 1
    local final_perc
    if awk -v tf="$total_energy_full" 'BEGIN{exit!(tf>0)}'; then final_perc=$(awk -v te="$total_energy" -v tf="$total_energy_full" 'BEGIN{printf "%.0f", (te/tf)*100}')
    else final_perc=$(awk -v sp="$sum_perc" -v c="$count" 'BEGIN{printf "%.0f", sp/c}'); fi
    is_integer "${final_perc%%.*}" || return 1; final_perc="${final_perc%%.*}"; (( final_perc < 0 )) && final_perc=0; (( final_perc > 100 )) && final_perc=100
    local final_state="Unknown"; if [[ "$any_charging" == "true" ]]; then final_state="Charging"; elif [[ "$any_discharging" == "true" ]]; then final_state="Discharging"; elif [[ "$any_full" == "true" ]]; then final_state="Full"; fi
    printf '%s;%s;%s' "$final_state" "$final_perc" "aggregate:$count"
}
do_suspend() {
    log "Attempting suspend (ignore inhibitors for critical)"
    if busctl --system call org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager SuspendWithFlags "t" 1 2>&1; then return 0; fi
    if busctl --system call org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager Suspend "b" false 2>&1; then return 0; fi
    if command -v systemctl >/dev/null; then systemctl suspend --no-block 2>&1 && return 0; systemctl suspend 2>&1 && return 0; fi
    return 1
}
startup_checks() {
    local errors=0
    command -v upower &>/dev/null || { log "Missing upower"; ((errors++)); }
    command -v notify-send &>/dev/null && HAS_NOTIFY=true
    command -v paplay &>/dev/null && HAS_PAPLAY=true
    command -v pw-play &>/dev/null && HAS_PWPLAY=true
    for var in BATTERY_FULL_THRESHOLD BATTERY_LOW_THRESHOLD BATTERY_CRITICAL_THRESHOLD BATTERY_UNPLUG_THRESHOLD REPEAT_FULL_MIN REPEAT_LOW_MIN REPEAT_CRITICAL_MIN SUSPEND_GRACE_SEC SAFETY_POLL_INTERVAL; do local val="${!var}"; is_integer "$val" || { log "Invalid $var='$val'"; ((errors++)); }; done
    (( 10#$BATTERY_FULL_THRESHOLD < 1 || 10#$BATTERY_FULL_THRESHOLD > 100 )) && { log "FULL 1..100"; ((errors++)); }
    (( 10#$BATTERY_LOW_THRESHOLD < 0 || 10#$BATTERY_LOW_THRESHOLD > 100 )) && { log "LOW 0..100"; ((errors++)); }
    (( 10#$BATTERY_CRITICAL_THRESHOLD < 0 || 10#$BATTERY_CRITICAL_THRESHOLD > 100 )) && { log "CRITICAL 0..100"; ((errors++)); }
    (( 10#$SAFETY_POLL_INTERVAL < 5 || 10#$SAFETY_POLL_INTERVAL > 600 )) && { log "POLL 5..600"; ((errors++)); }
    (( 10#$SUSPEND_GRACE_SEC < 0 || 10#$SUSPEND_GRACE_SEC > 3600 )) && { log "GRACE 0..3600"; ((errors++)); }
    (( 10#$BATTERY_CRITICAL_THRESHOLD >= 10#$BATTERY_LOW_THRESHOLD )) && { log "FATAL: CRITICAL must < LOW"; ((errors++)); }
    (( 10#$BATTERY_LOW_THRESHOLD >= 10#$BATTERY_FULL_THRESHOLD )) && { log "FATAL: LOW must < FULL"; ((errors++)); }
    (( errors > 0 )) && return 1; return 0
}
process_battery_event() {
    local state="$1" percentage="$2" mono_now="$3"
    percentage="${percentage%%.*}"; is_integer "$percentage" || return 0
    [[ "$state" == "Charging" || "$state" == "Full" ]] && STATE_LAST_SUSPEND_MONO=0
    if [[ "$STATE_LAST" == "Charging" || "$STATE_LAST" == "Full" ]] && [[ "$state" == "Discharging" || "$state" == "Empty" ]]; then
        (( 10#$percentage <= 10#$BATTERY_UNPLUG_THRESHOLD )) && fn_notify "normal" "Power Disconnected" "$percentage% — running on battery" "battery-ac-adapter" "$SOUND_UNPLUG"
    fi
    if [[ "$STATE_LAST" == "Discharging" || "$STATE_LAST" == "Empty" ]] && [[ "$state" == "Charging" ]]; then
        fn_notify "normal" "Power Connected" "$percentage% — charging" "$(get_icon "$percentage" "$state")" "$SOUND_PLUG"
    fi
    if [[ "$STATE_LAST" != "Full" && "$state" == "Full" ]] || { [[ "$state" == "Charging" ]] && (( 10#$percentage >= 10#$BATTERY_FULL_THRESHOLD )) && (( 10#$STATE_LAST_PERCENTAGE < 10#$BATTERY_FULL_THRESHOLD )); }; then
        local now_diff=$(( 10#$mono_now - 10#$STATE_LAST_FULL_NOTIFY ))
        if (( 10#$STATE_LAST_FULL_NOTIFY == 0 || now_diff >= 10#$REPEAT_FULL_MIN * 60 )); then
            fn_notify "normal" "Battery Fully Charged" "$percentage% capacity" "battery-full-charged" "$SOUND_PLUG"; STATE_LAST_FULL_NOTIFY=$mono_now
        fi
    fi
    if [[ "$state" == "Discharging" || "$state" == "Empty" ]]; then
        if (( 10#$percentage <= 10#$BATTERY_LOW_THRESHOLD && 10#$percentage > 10#$BATTERY_CRITICAL_THRESHOLD )); then
            local crossed=false; (( 10#$STATE_LAST_PERCENTAGE > 10#$BATTERY_LOW_THRESHOLD )) && crossed=true
            local elapsed=$(( 10#$mono_now - 10#$STATE_LAST_LOW_NOTIFY ))
            if [[ "$crossed" == "true" ]] || (( 10#$STATE_LAST_LOW_NOTIFY == 0 || elapsed >= 10#$REPEAT_LOW_MIN * 60 )); then
                fn_notify "normal" "Battery Level Low" "$percentage% capacity remaining" "battery-caution" "$SOUND_LOW"; STATE_LAST_LOW_NOTIFY=$mono_now
            fi
        fi
    fi
    if [[ "$state" == "Discharging" || "$state" == "Empty" ]] && (( 10#$percentage <= 10#$BATTERY_CRITICAL_THRESHOLD )); then
        local in_grace=false grace_rem=0
        if (( 10#$STATE_LAST_SUSPEND_MONO > 0 )); then local diff=$(( 10#$mono_now - 10#$STATE_LAST_SUSPEND_MONO )); grace_rem=$(( 10#$SUSPEND_GRACE_SEC - diff )); (( grace_rem > 0 )) && in_grace=true; fi
        local crossed_crit=false; (( 10#$STATE_LAST_PERCENTAGE > 10#$BATTERY_CRITICAL_THRESHOLD )) && crossed_crit=true
        local elapsed_c=$(( 10#$mono_now - 10#$STATE_LAST_CRITICAL_NOTIFY ))
        if [[ "$crossed_crit" == "true" ]] || (( 10#$STATE_LAST_CRITICAL_NOTIFY == 0 || elapsed_c >= 10#$REPEAT_CRITICAL_MIN * 60 )); then
            local msg; if [[ "$in_grace" == "true" ]]; then msg="$percentage% — suspend in ${grace_rem}s! Save work!"; else msg="$percentage% — $MSG_CRITICAL"; fi
            fn_notify "critical" "Battery Status Critical" "$msg" "battery-empty" "$SOUND_CRITICAL"; STATE_LAST_CRITICAL_NOTIFY=$mono_now
        fi
        if [[ "$DO_SUSPEND" == "true" && "$in_grace" == "false" ]]; then
            log "Critical $percentage% — will suspend in 2s (re-checking)"; sleep 2
            local reread state2 perc2 mode2
            if reread=$(read_battery_aggregated); then
                IFS=';' read -r state2 perc2 mode2 <<< "$reread"; perc2="${perc2%%.*}"
                if [[ "$state2" == "Charging" || "$state2" == "Full" ]]; then log "Abort suspend — now $state2"
                elif is_integer "$perc2" && (( 10#$perc2 > 10#$BATTERY_CRITICAL_THRESHOLD )); then log "Abort suspend — now $perc2% > critical"
                else log "Executing suspend"; if do_suspend; then STATE_LAST_SUSPEND_MONO=$(get_mono_now); log "Resumed — grace ${SUSPEND_GRACE_SEC}s"; else log "Suspend failed"; fi; fi
            else log "Re-read failed, suspending anyway"; if do_suspend; then STATE_LAST_SUSPEND_MONO=$(get_mono_now); fi; fi
        elif [[ "$in_grace" == "true" ]]; then log "Grace active ${grace_rem}s"; fi
    fi
    STATE_LAST="$state"; STATE_LAST_PERCENTAGE=$percentage
}
reset_state() { STATE_LAST=""; STATE_LAST_PERCENTAGE=999; STATE_LAST_FULL_NOTIFY=0; STATE_LAST_LOW_NOTIFY=0; STATE_LAST_CRITICAL_NOTIFY=0; STATE_LAST_SUSPEND_MONO=0; }
start_monitor() {
    if (( MON_FD >= 0 )); then exec {MON_FD}<&- 2>/dev/null || true; MON_FD=-1; fi
    coproc UPMON { exec upower --monitor 2>/dev/null; }; UPMON_PID=$!
    if [[ -n "${UPMON[0]:-}" ]]; then exec {MON_FD}<&${UPMON[0]}; else MON_FD=-1; fi
    log "Monitor started PID=$UPMON_PID fd=$MON_FD mode=$CURRENT_MODE poll=${SAFETY_POLL_INTERVAL}s"
}
stop_monitor() {
    if (( MON_FD >= 0 )); then exec {MON_FD}<&- 2>/dev/null || true; MON_FD=-1; fi
    if [[ -n "$UPMON_PID" ]] && kill -0 "$UPMON_PID" 2>/dev/null; then kill "$UPMON_PID" 2>/dev/null || true; wait "$UPMON_PID" 2>/dev/null || true; fi
    UPMON_PID=""
}
cleanup() { [[ "$RUNNING" == "true" ]] && log "Shutting down"; RUNNING=false; stop_monitor; }
trap cleanup EXIT TERM INT HUP
main_loop() {
    reset_state; local retry=0 reading
    while ! reading=$(read_battery_aggregated); do
        ((retry++)); (( retry >= MAX_RETRIES )) && die "No battery after $MAX_RETRIES"
        log "No battery yet (attempt $retry/$MAX_RETRIES), retrying 2s..."; sleep 2
    done
    local state perc mode; IFS=';' read -r state perc mode <<< "$reading"; CURRENT_MODE="$mode"
    local mono_now; mono_now=$(get_mono_now)
    log "Initial: $state $perc% ($CURRENT_MODE) thresholds Full=$BATTERY_FULL_THRESHOLD% Low=$BATTERY_LOW_THRESHOLD% Critical=$BATTERY_CRITICAL_THRESHOLD%"
    STATE_LAST="$state"; process_battery_event "$state" "$perc" "$mono_now"
    start_monitor
    local line
    while [[ "$RUNNING" == "true" ]]; do
        if IFS= read -r -t "$SAFETY_POLL_INTERVAL" -u "$MON_FD" line; then
            sleep 0.1; while IFS= read -r -t 0.05 -u "$MON_FD" _discard; do :; done
            if reading=$(read_battery_aggregated); then IFS=';' read -r state perc mode <<< "$reading"; CURRENT_MODE="$mode"; mono_now=$(get_mono_now); process_battery_event "$state" "$perc" "$mono_now"; fi
        else
            local rc=$?
            if (( rc > 128 )); then
                if reading=$(read_battery_aggregated); then IFS=';' read -r state perc mode <<< "$reading"; CURRENT_MODE="$mode"; mono_now=$(get_mono_now); process_battery_event "$state" "$perc" "$mono_now"; fi
            else log "Monitor died rc=$rc, restarting"; stop_monitor; sleep 0.5; start_monitor; fi
        fi
        if [[ -n "$UPMON_PID" ]] && ! kill -0 "$UPMON_PID" 2>/dev/null; then log "Monitor PID vanished, restarting"; stop_monitor; start_monitor; fi
    done
}
main() {
    log "=== Battery Monitor Starting PID=$$ (Hyprland-only) ==="
    startup_checks || die "Startup checks failed"
    main_loop
    log "=== Stopped ==="
}
main "$@"
