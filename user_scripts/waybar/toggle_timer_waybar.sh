#!/bin/bash
#
# waybar_timer.sh - Run Waybar for a specified duration with guaranteed cleanup
#
# Usage: waybar_timer.sh [DURATION_IN_SECONDS]
#

# ==============================================================================
# STRICT MODE
# ==============================================================================
set -uo pipefail

# ==============================================================================
# CONFIGURATION
# ==============================================================================
readonly SCRIPT_NAME="${0##*/}"
readonly DURATION="${1:-60}"
readonly LOCK_FILE="${XDG_RUNTIME_DIR:-/run/user/${UID:-$(id -u)}}/waybar_timer.lock"
readonly STARTUP_GRACE_PERIOD=1
readonly KILL_GRACE_PERIOD=1

# ==============================================================================
# RUNTIME STATE
# ==============================================================================
WAYBAR_PID=""
CLEANUP_EXECUTED=0

# ==============================================================================
# LOGGING FUNCTIONS
# ==============================================================================
log_info() {
    printf '[%s] %s\n' "$SCRIPT_NAME" "$*"
}

log_error() {
    printf '[%s] ERROR: %s\n' "$SCRIPT_NAME" "$*" >&2
}

# ==============================================================================
# CLEANUP FUNCTION
# ==============================================================================
cleanup() {
    # Guard: Prevent running multiple times (critical for signal + EXIT combo)
    if (( CLEANUP_EXECUTED )); then
        return 0
    fi
    CLEANUP_EXECUTED=1

    log_info "Initiating cleanup..."

    # Only kill the specific PID we started - never killall
    if [[ -n "${WAYBAR_PID:-}" ]]; then
        if kill -0 "$WAYBAR_PID" 2>/dev/null; then
            log_info "Sending SIGTERM to PID $WAYBAR_PID..."
            kill -TERM "$WAYBAR_PID" 2>/dev/null || true

            # Wait for graceful termination
            local waited=0
            while (( waited < KILL_GRACE_PERIOD * 10 )); do
                if ! kill -0 "$WAYBAR_PID" 2>/dev/null; then
                    log_info "Process terminated gracefully."
                    break
                fi
                sleep 0.1
                (( waited++ )) || true
            done

            # Escalate to SIGKILL if still alive
            if kill -0 "$WAYBAR_PID" 2>/dev/null; then
                log_info "Process didn't terminate, sending SIGKILL..."
                kill -KILL "$WAYBAR_PID" 2>/dev/null || true
            fi

            # Reap zombie process (we are the parent)
            wait "$WAYBAR_PID" 2>/dev/null || true
        else
            log_info "Process $WAYBAR_PID already terminated."
        fi
    fi

    # Release lock and remove lock file
    rm -f "$LOCK_FILE" 2>/dev/null || true

    log_info "Cleanup complete."
}

# ==============================================================================
# TRAP SETUP
# ==============================================================================
setup_traps() {
    # EXIT handles natural script end
    trap cleanup EXIT
    # Explicit signal handling ensures cleanup runs then exits with correct code
    trap 'cleanup; exit 130' INT      # 128 + 2
    trap 'cleanup; exit 143' TERM     # 128 + 15
    trap 'cleanup; exit 131' QUIT     # 128 + 3
}

# ==============================================================================
# VALIDATION FUNCTIONS
# ==============================================================================
validate_environment() {
    # Check required commands
    local missing=()
    local cmd
    for cmd in pgrep uwsm-app timeout tail flock; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done

    if (( ${#missing[@]} > 0 )); then
        log_error "Missing required commands: ${missing[*]}"
        return 1
    fi

    # Validate DURATION is a positive integer
    if ! [[ "$DURATION" =~ ^[1-9][0-9]*$ ]]; then
        log_error "DURATION must be a positive integer (got: '$DURATION')"
        return 1
    fi

    # Verify lock directory exists
    local lock_dir
    lock_dir="$(dirname "$LOCK_FILE")"
    if [[ ! -d "$lock_dir" ]]; then
        log_error "Lock directory does not exist: $lock_dir"
        log_error "Is XDG_RUNTIME_DIR set correctly?"
        return 1
    fi

    return 0
}

# ==============================================================================
# LOCKING
# ==============================================================================
acquire_lock() {
    # Open file descriptor 200 for locking
    if ! exec 200>"$LOCK_FILE"; then
        log_error "Cannot create lock file: $LOCK_FILE"
        return 1
    fi

    # Attempt non-blocking exclusive lock
    if ! flock -n 200; then
        log_error "Another instance of this script is already running."
        log_error "Lock file: $LOCK_FILE"
        return 1
    fi

    # Write PID to lock file for debugging
    echo $$ >&200

    return 0
}

# ==============================================================================
# WAYBAR MANAGEMENT
# ==============================================================================
check_existing_waybar() {
    if pgrep -x "waybar" >/dev/null 2>&1; then
        log_error "Waybar is already running (found via pgrep)."
        log_error "This script refuses to interfere with existing instances."
        log_error "Stop the existing Waybar first, or manage it separately."
        return 1
    fi
    return 0
}

start_waybar() {
    log_info "Launching Waybar via uwsm-app..."

    # Start uwsm-app with waybar
    # Note: If uwsm-app execs waybar, $! will be waybar's PID
    #       If uwsm-app forks, we track uwsm-app (still useful for cleanup)
    uwsm-app -- waybar &
    WAYBAR_PID=$!

    log_info "Started process with PID: $WAYBAR_PID"

    # Allow startup time
    sleep "$STARTUP_GRACE_PERIOD"

    # Verify our child process is still running
    if ! kill -0 "$WAYBAR_PID" 2>/dev/null; then
        log_error "Process $WAYBAR_PID died during startup."
        log_error "Check waybar configuration for errors."
        return 1
    fi

    # Additional verification: is waybar actually running?
    if ! pgrep -x "waybar" >/dev/null 2>&1; then
        log_error "uwsm-app started but waybar process not detected."
        log_error "The launcher may have failed silently."
        return 1
    fi

    log_info "Waybar is running. Starting ${DURATION}s countdown..."
    return 0
}

monitor_waybar() {
    # Block until either:
    #   - DURATION seconds pass (timeout returns 124)
    #   - WAYBAR_PID disappears (tail --pid returns 0)
    #
    # tail -f /dev/null blocks forever; --pid makes it exit when PID dies
    # timeout wraps it with our duration limit

    local monitor_status=0
    timeout "$DURATION" tail --pid="$WAYBAR_PID" -f /dev/null 2>/dev/null || monitor_status=$?

    case $monitor_status in
        0)
            log_info "Waybar process exited on its own (or was killed externally)."
            ;;
        124)
            log_info "Time limit reached (${DURATION}s). Stopping Waybar..."
            ;;
        137)
            # timeout itself was killed (128 + 9 = SIGKILL)
            log_info "Monitoring was forcefully terminated."
            ;;
        *)
            log_info "Monitoring ended with unexpected status: $monitor_status"
            ;;
    esac

    return $monitor_status
}

# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================
main() {
    log_info "=== Waybar Timer Script ==="
    log_info "Duration: ${DURATION}s"

    # Phase 1: Validation (before we acquire any resources)
    if ! validate_environment; then
        exit 1
    fi

    # Phase 2: Acquire exclusive lock
    if ! acquire_lock; then
        exit 1
    fi

    # Phase 3: Set up cleanup traps (now that we own resources)
    setup_traps

    # Phase 4: Ensure no existing waybar
    if ! check_existing_waybar; then
        exit 1
    fi

    # Phase 5: Start waybar
    if ! start_waybar; then
        exit 1
    fi

    # Phase 6: Monitor until timeout or external termination
    monitor_waybar
    local final_status=$?

    # Phase 7: Cleanup runs automatically via EXIT trap

    log_info "=== Script finished ==="
    exit $final_status
}

# Run main with all arguments
main "$@"
