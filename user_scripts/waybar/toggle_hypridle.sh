#!/usr/bin/env bash
#
# Toggle hypridle (idle inhibitor) on/off
# Integrates with Waybar custom module via real-time signals
#

# Exit on unset variables, propagate pipe failures
set -uo pipefail

# =============================================================================
# Configuration
# =============================================================================
readonly WAYBAR_SIGNAL=9              # Must match "signal" in waybar config
readonly PROC_NAME="hypridle"
readonly KILL_TIMEOUT=50              # Iterations (50 × 100ms = 5 seconds)

# =============================================================================
# Helper Functions
# =============================================================================
is_running() {
    pgrep -x "${PROC_NAME}" &>/dev/null
}

send_notification() {
    local urgency="$1"
    local title="$2"
    local body="$3"
    local icon="$4"

    # Silently skip if notify-send unavailable
    command -v notify-send &>/dev/null || return 0
    notify-send -u "${urgency}" -t 2000 "${title}" "${body}" -i "${icon}"
}

update_waybar() {
    # Signal Waybar to refresh the module; ignore if Waybar not running
    pkill -RTMIN+"${WAYBAR_SIGNAL}" waybar 2>/dev/null || true
}

# =============================================================================
# Main Logic
# =============================================================================
main() {
    if is_running; then
        # -----------------------------------------------------------------
        # DISABLE hypridle (Coffee Mode)
        # -----------------------------------------------------------------
        
        # Send SIGTERM (graceful shutdown)
        pkill -x "${PROC_NAME}" 2>/dev/null || true

        # Wait for termination with timeout
        local count=0
        while is_running && (( count < KILL_TIMEOUT )); do
            sleep 0.1
            ((count++))
        done

        # Force kill if still alive (SIGKILL)
        if is_running; then
            pkill -9 -x "${PROC_NAME}" 2>/dev/null || true
            sleep 0.2
        fi

        # Final verification
        if is_running; then
            send_notification "critical" "Error" \
                "Failed to stop ${PROC_NAME}" "dialog-error"
            exit 1
        fi

        send_notification "low" "Suspend Inhibited" \
            "Automatic suspend is now OFF (Coffee Mode ☕)." \
            "dialog-warning"
    else
        # -----------------------------------------------------------------
        # ENABLE hypridle
        # -----------------------------------------------------------------
        
        # Verify binary exists
        if ! command -v "${PROC_NAME}" &>/dev/null; then
            send_notification "critical" "Error" \
                "${PROC_NAME} not found in PATH" "dialog-error"
            exit 1
        fi

        # Start in background, detach from script
        "${PROC_NAME}" &>/dev/null &
        disown

        # Allow time for process to initialize
        sleep 0.3

        # Verify it started successfully
        if ! is_running; then
            send_notification "critical" "Error" \
                "Failed to start ${PROC_NAME}" "dialog-error"
            exit 1
        fi

        send_notification "low" "Suspend Enabled" \
            "Automatic suspend is now ON." \
            "dialog-information"
    fi

    # Update Waybar module
    update_waybar
}

main "$@"
exit 0
