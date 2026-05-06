#!/usr/bin/env bash
#
# Battery Notification Script - Event-Driven Edition
# Uses upower --monitor for instant, efficient updates
#

set -uo pipefail

##########################
# CONFIGURATION
##########################
readonly BATTERY_DEVICE="${BATTERY_DEVICE:-}"

# Thresholds (ensure CRITICAL < LOW < FULL)
readonly BATTERY_FULL_THRESHOLD="${BATTERY_FULL_THRESHOLD:-100}"
readonly BATTERY_LOW_THRESHOLD="${BATTERY_LOW_THRESHOLD:-20}"
readonly BATTERY_CRITICAL_THRESHOLD="${BATTERY_CRITICAL_THRESHOLD:-10}"
readonly BATTERY_UNPLUG_THRESHOLD="${BATTERY_UNPLUG_THRESHOLD:-100}"

# Repeat notification timers (minutes)
readonly REPEAT_FULL_MIN="${REPEAT_FULL_MIN:-999}"
readonly REPEAT_LOW_MIN="${REPEAT_LOW_MIN:-3}"
readonly REPEAT_CRITICAL_MIN="${REPEAT_CRITICAL_MIN:-1}"

# Grace period after waking from critical suspend (seconds)
# Gives user time to save work before next suspend
readonly SUSPEND_GRACE_SEC="${SUSPEND_GRACE_SEC:-60}"

# Safety poll interval (seconds) - fallback if monitor misses events
readonly SAFETY_POLL_INTERVAL="${SAFETY_POLL_INTERVAL:-60}"

# Commands & Sounds
readonly CMD_CRITICAL="${CMD_CRITICAL:-systemctl suspend}"
readonly MSG_CRITICAL="${MSG_CRITICAL:-Suspending system!}"
readonly SOUND_LOW="${SOUND_LOW:-/usr/share/sounds/freedesktop/stereo/complete.oga}"
readonly SOUND_CRITICAL="${SOUND_CRITICAL:-/usr/share/sounds/freedesktop/stereo/suspend-error.oga}"
readonly SOUND_PLUG="${SOUND_PLUG:-/usr/share/sounds/freedesktop/stereo/device-added.oga}"
readonly SOUND_UNPLUG="${SOUND_UNPLUG:-/usr/share/sounds/freedesktop/stereo/device-removed.oga}"

readonly MAX_RETRIES=5

##########################
# RUNTIME STATE (Global)
##########################
# Process control
declare -g RUNNING=true
declare -g CURRENT_DEVICE=""
declare -g MONITOR_PID=""

# Capability cache (set during startup)
declare -g HAS_NOTIFY_SEND=false
declare -g HAS_PAPLAY=false
declare -g HAS_EPOCHSECONDS=false

# Battery state tracking (explicitly global - modified by process_battery_event)
declare -g STATE_LAST=""
declare -g STATE_LAST_PERCENTAGE=999
declare -g STATE_LAST_FULL_NOTIFY=0
declare -g STATE_LAST_LOW_NOTIFY=0
declare -g STATE_LAST_CRITICAL_NOTIFY=0
# Timestamp of last suspend (0 = never suspended this discharge cycle)
declare -g STATE_LAST_SUSPEND_TIME=0

##########################
# SIGNAL HANDLING
##########################
cleanup() {
    log "Received signal, shutting down gracefully..."
    RUNNING=false
    
    # Kill monitor process if running
    if [[ -n "$MONITOR_PID" ]] && kill -0 "$MONITOR_PID" 2>/dev/null; then
        kill "$MONITOR_PID" 2>/dev/null
        wait "$MONITOR_PID" 2>/dev/null
    fi
}
trap cleanup SIGTERM SIGINT SIGHUP

##########################
# HELPER FUNCTIONS
##########################

log() {
    printf '[%s] [battery_notify] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

die() {
    log "FATAL: $*"
    exit 1
}

is_integer() {
    local val="${1:-}"
    [[ -n "$val" && "$val" =~ ^[0-9]+$ ]]
}

get_timestamp() {
    if [[ "$HAS_EPOCHSECONDS" == "true" ]]; then
        printf '%s' "$EPOCHSECONDS"
    else
        date +%s
    fi
}

get_icon() {
    local perc="${1:-0}"
    local state="${2:-Discharging}"
    
    is_integer "$perc" || perc=0
    ((perc > 100)) && perc=100
    
    local rounded=$(( (perc + 5) / 10 * 10 ))
    ((rounded > 100)) && rounded=100
    
    if [[ "$state" == "Charging" ]]; then
        printf '%s' "battery-level-${rounded}-charging-symbolic"
    else
        printf '%s' "battery-level-${rounded}-symbolic"
    fi
}

fn_notify() {
    local urgency="${1:-normal}"
    local title="${2:-Notification}"
    local body="${3:-}"
    local icon="${4:-battery-symbolic}"
    local sound="${5:-}"

    if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
        export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus"
    fi

    if [[ "$HAS_NOTIFY_SEND" == "true" ]]; then
        notify-send -u "$urgency" -t 5000 -a "Battery Monitor" -i "$icon" \
            "$title" "$body" 2>/dev/null || log "Warning: notify-send failed"
    else
        log "Notification: [$urgency] $title - $body"
    fi

    if [[ -n "$sound" && -f "$sound" && "$HAS_PAPLAY" == "true" ]]; then
        ( paplay "$sound" >/dev/null 2>&1 & )
    fi
}

detect_battery() {
    local dev=""
    
    if [[ -n "$BATTERY_DEVICE" ]]; then
        if upower -i "$BATTERY_DEVICE" &>/dev/null; then
            printf '%s' "$BATTERY_DEVICE"
            return 0
        fi
        log "Warning: Configured device '$BATTERY_DEVICE' not found"
        return 1
    fi
    
    dev=$(upower -e 2>/dev/null | grep -m1 -iE 'BAT|battery')
    
    if [[ -z "$dev" ]]; then
        return 1
    fi
    
    printf '%s' "$dev"
    return 0
}

read_battery() {
    local dev="$1"
    local info state perc
    
    info=$(upower -i "$dev" 2>/dev/null) || return 1
    [[ -z "$info" ]] && return 1
    
    read -r state perc < <(awk -F: '
        /^[[:space:]]*state:/ {
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2)
            st = $2
        }
        /^[[:space:]]*percentage:/ {
            gsub(/[^0-9]/, "", $2)
            pct = $2
        }
        END {
            print st, pct
        }
    ' <<< "$info")
    
    if ! is_integer "$perc"; then
        log "Warning: Invalid percentage '$perc'"
        return 1
    fi
    
    case "${state,,}" in
        discharging|"not charging"|not-charging)  state="Discharging" ;;
        charging|"pending charge"|pending-charge) state="Charging" ;;
        "fully charged"|fully-charged|full)       state="Full" ;;
        empty)                                    state="Empty" ;;
        *)                                        state="Unknown" ;;
    esac
    
    printf '%s;%s' "$state" "$perc"
    return 0
}

##########################
# STARTUP VALIDATION
##########################
startup_checks() {
    local errors=0
    
    if [[ -n "${EPOCHSECONDS:-}" ]] || ((BASH_VERSINFO[0] >= 5)); then
        HAS_EPOCHSECONDS=true
    fi
    
    for cmd in upower awk grep; do
        if ! command -v "$cmd" &>/dev/null; then
            log "Missing required command: $cmd"
            ((errors++))
        fi
    done
    
    command -v notify-send &>/dev/null && HAS_NOTIFY_SEND=true
    command -v paplay &>/dev/null && HAS_PAPLAY=true
    
    [[ "$HAS_NOTIFY_SEND" == "false" ]] && log "Warning: notify-send not found - logging only"
    [[ "$HAS_PAPLAY" == "false" ]] && log "Warning: paplay not found - no sounds"
    
    for var in BATTERY_FULL_THRESHOLD BATTERY_LOW_THRESHOLD BATTERY_CRITICAL_THRESHOLD \
               BATTERY_UNPLUG_THRESHOLD REPEAT_FULL_MIN REPEAT_LOW_MIN REPEAT_CRITICAL_MIN \
               SUSPEND_GRACE_SEC SAFETY_POLL_INTERVAL; do
        if ! is_integer "${!var}"; then
            log "Invalid configuration: $var='${!var}' (must be integer)"
            ((errors++))
        fi
    done
    
    local sound_var
    for sound_var in SOUND_LOW SOUND_CRITICAL SOUND_PLUG SOUND_UNPLUG; do
        local sound_path="${!sound_var}"
        [[ -n "$sound_path" && ! -f "$sound_path" ]] && \
            log "Warning: Sound file not found: $sound_path"
    done

    if ((BATTERY_CRITICAL_THRESHOLD >= BATTERY_LOW_THRESHOLD)); then
        log "Warning: CRITICAL ($BATTERY_CRITICAL_THRESHOLD) >= LOW ($BATTERY_LOW_THRESHOLD)"
    fi
    if ((BATTERY_LOW_THRESHOLD >= BATTERY_FULL_THRESHOLD)); then
        log "Warning: LOW ($BATTERY_LOW_THRESHOLD) >= FULL ($BATTERY_FULL_THRESHOLD)"
    fi
    
    return "$errors"
}

##########################
# EVENT PROCESSOR
##########################
# Modifies global STATE_* variables
process_battery_event() {
    local state="$1"
    local percentage="$2"
    local now="$3"
    
    # Reset suspend timer when charging (fresh cycle)
    if [[ "$state" == "Charging" || "$state" == "Full" ]]; then
        STATE_LAST_SUSPEND_TIME=0
    fi
    
    # --- STATE TRANSITION ---
    if [[ "$state" != "$STATE_LAST" ]]; then
        log "State: '${STATE_LAST:-<init>}' -> '$state' ($percentage%)"
        
        case "$state" in
            Charging)
                fn_notify "normal" "⚡ Charging" \
                    "Battery is charging ($percentage%)" \
                    "$(get_icon "$percentage" "$state")" "$SOUND_PLUG"
                ;;
            Discharging)
                if ((percentage <= BATTERY_UNPLUG_THRESHOLD)); then
                    fn_notify "normal" "🔋 Unplugged" \
                        "Running on battery ($percentage%)" \
                        "$(get_icon "$percentage" "$state")" "$SOUND_UNPLUG"
                fi
                ;;
            Full)
                fn_notify "normal" "✓ Fully Charged" \
                    "Battery at ${percentage}%" \
                    "battery-full-charged-symbolic" ""
                STATE_LAST_FULL_NOTIFY=$now
                ;;
            Empty)
                fn_notify "critical" "⚠️ Battery Empty" \
                    "System may shut down!" \
                    "battery-empty-symbolic" "$SOUND_CRITICAL"
                ;;
        esac
        STATE_LAST="$state"
    fi
    
    # --- FULL NOTIFICATION (while charging at/above threshold) ---
    if [[ "$state" == "Charging" ]] && ((percentage >= BATTERY_FULL_THRESHOLD)); then
        if ((now - STATE_LAST_FULL_NOTIFY >= REPEAT_FULL_MIN * 60)); then
            fn_notify "normal" "🔋 Battery Full" \
                "Level: $percentage% - Congratulations!" \
                "battery-full-charged-symbolic" ""
            STATE_LAST_FULL_NOTIFY=$now
        fi
    fi
    
    # --- LOW NOTIFICATION ---
    if [[ "$state" == "Discharging" ]] && ((percentage <= BATTERY_LOW_THRESHOLD)); then
        if ((percentage > BATTERY_CRITICAL_THRESHOLD)); then
            if ((STATE_LAST_PERCENTAGE > BATTERY_LOW_THRESHOLD)) || \
               ((now - STATE_LAST_LOW_NOTIFY >= REPEAT_LOW_MIN * 60)); then
                fn_notify "normal" "⚠️ Battery Low" \
                    "$percentage% remaining" \
                    "$(get_icon "$percentage" "$state")" "$SOUND_LOW"
                STATE_LAST_LOW_NOTIFY=$now
            fi
        fi
    fi
    
    # --- CRITICAL NOTIFICATION & ACTION ---
    if [[ "$state" == "Discharging" ]] && ((percentage <= BATTERY_CRITICAL_THRESHOLD)); then
        
        # Calculate grace period status
        local in_grace_period=false
        local grace_remaining=0
        
        if ((STATE_LAST_SUSPEND_TIME > 0)); then
            grace_remaining=$((SUSPEND_GRACE_SEC - (now - STATE_LAST_SUSPEND_TIME)))
            ((grace_remaining > 0)) && in_grace_period=true
        fi
        
        # Notification (on threshold crossing or repeat timer)
        if ((STATE_LAST_PERCENTAGE > BATTERY_CRITICAL_THRESHOLD)) || \
           ((now - STATE_LAST_CRITICAL_NOTIFY >= REPEAT_CRITICAL_MIN * 60)); then
            
            local notify_msg
            if [[ "$in_grace_period" == "true" ]]; then
                notify_msg="$percentage% - Suspending in ${grace_remaining}s! Save your work!"
            else
                notify_msg="$percentage% - $MSG_CRITICAL"
            fi
            
            fn_notify "critical" "🚨 CRITICAL BATTERY" \
                "$notify_msg" \
                "battery-level-0-symbolic" "$SOUND_CRITICAL"
            STATE_LAST_CRITICAL_NOTIFY=$now
        fi
        
        # Suspend action (only if NOT in grace period)
        if [[ -n "$CMD_CRITICAL" && "$in_grace_period" == "false" ]]; then
            log "Executing: $CMD_CRITICAL"
            sleep 2  # Let user see notification
            
            if eval "$CMD_CRITICAL"; then
                # Record wake time - grace period starts NOW
                STATE_LAST_SUSPEND_TIME=$(get_timestamp)
                log "System resumed - grace period started (${SUSPEND_GRACE_SEC}s)"
            else
                log "Critical command failed (exit: $?)"
                # Don't set suspend time - will retry on next event
            fi
        elif [[ "$in_grace_period" == "true" ]]; then
            log "Grace period active: ${grace_remaining}s remaining"
        fi
    fi
    
    STATE_LAST_PERCENTAGE=$percentage
}

##########################
# STATE RESET FUNCTION
##########################
reset_state() {
    STATE_LAST=""
    STATE_LAST_PERCENTAGE=999
    STATE_LAST_FULL_NOTIFY=0
    STATE_LAST_LOW_NOTIFY=0
    STATE_LAST_CRITICAL_NOTIFY=0
    STATE_LAST_SUSPEND_TIME=0
}

##########################
# MAIN LOOP (Event-Driven)
##########################
main_loop() {
    local reading state percentage now
    
    # Reset global state
    reset_state
    
    # Initial detection with retries
    local retry=0
    while [[ "$RUNNING" == "true" ]] && ! CURRENT_DEVICE=$(detect_battery); do
        ((retry++))
        if ((retry >= MAX_RETRIES)); then
            die "No battery found after $MAX_RETRIES attempts"
        fi
        log "Detection failed (attempt $retry/$MAX_RETRIES), retrying..."
        sleep 2
    done
    
    [[ "$RUNNING" != "true" ]] && return 0
    
    log "Monitoring: $CURRENT_DEVICE"
    log "Mode: Event-driven (upower --monitor) with ${SAFETY_POLL_INTERVAL}s safety poll"
    log "Thresholds: Full=${BATTERY_FULL_THRESHOLD}% Low=${BATTERY_LOW_THRESHOLD}% Critical=${BATTERY_CRITICAL_THRESHOLD}%"
    log "Grace period after wake: ${SUSPEND_GRACE_SEC}s"

    # Get initial battery state
    if reading=$(read_battery "$CURRENT_DEVICE"); then
        state="${reading%%;*}"
        percentage="${reading##*;}"
        now=$(get_timestamp)
        
        log "Initial state: $state at $percentage%"
        process_battery_event "$state" "$percentage" "$now"
    else
        log "Warning: Could not read initial battery state"
    fi
    
    # Create named pipe for monitor
    local monitor_fifo
    monitor_fifo=$(mktemp -u)
    mkfifo "$monitor_fifo"
    
    trap 'rm -f "$monitor_fifo"' EXIT
    
    # Start monitor process
    upower --monitor 2>/dev/null > "$monitor_fifo" &
    MONITOR_PID=$!
    
    log "Monitor started (PID: $MONITOR_PID)"
    
    local line
    while [[ "$RUNNING" == "true" ]]; do
        if read -r -t "$SAFETY_POLL_INTERVAL" line < "$monitor_fifo"; then
            if [[ "$line" == *"$CURRENT_DEVICE"* || "$line" == *"battery"* ]]; then
                sleep 0.1
                
                if reading=$(read_battery "$CURRENT_DEVICE"); then
                    state="${reading%%;*}"
                    percentage="${reading##*;}"
                    now=$(get_timestamp)
                    
                    process_battery_event "$state" "$percentage" "$now"
                fi
            fi
        else
            # Timeout - safety poll
            if reading=$(read_battery "$CURRENT_DEVICE"); then
                state="${reading%%;*}"
                percentage="${reading##*;}"
                now=$(get_timestamp)
                
                if [[ "$state" != "$STATE_LAST" || "$percentage" != "$STATE_LAST_PERCENTAGE" ]]; then
                    log "Safety poll detected change"
                    process_battery_event "$state" "$percentage" "$now"
                fi
            else
                log "Safety poll: Read failed, attempting re-detection..."
                if CURRENT_DEVICE=$(detect_battery); then
                    log "Re-detected: $CURRENT_DEVICE"
                fi
            fi
        fi
        
        # Restart monitor if dead
        if ! kill -0 "$MONITOR_PID" 2>/dev/null; then
            log "Monitor process died, restarting..."
            upower --monitor 2>/dev/null > "$monitor_fifo" &
            MONITOR_PID=$!
        fi
    done
    
    log "Loop terminated gracefully"
}

##########################
# ENTRY POINT
##########################
main() {
    log "=== Battery Monitor Starting (PID: $$) ==="
    log "Version: Event-Driven (upower --monitor)"
    
    if ! startup_checks; then
        die "Startup checks failed"
    fi
    
    main_loop
    
    log "=== Battery Monitor Stopped ==="
    exit 0
}

main "$@"
