#!/usr/bin/env bash
# Hyprland Waybar Visibility Manager Script
# Version 3.2.0 (Corrected and Optimized)
#
# This script manages Waybar visibility in Hyprland based on Hyprland events:
# 1. Special Workspace: Waybar is hidden if on a specific "special" workspace.
# 2. Regular Workspaces: Waybar visibility based on active window's fullscreen state.
#
# Fixes from 3.1.0:
# - Added pgrep/pkill to dependency check
# - Fixed subshell variable scope by using process substitution instead of pipe
# - Fixed jq exit status capture issue
# - Proper stdin redirect and disown for backgrounded waybar
# - Improved locking mechanism with flock
# - Added XDG_RUNTIME_DIR validation
# - Eliminated TOCTOU race conditions
# - Removed unnecessary awk process

set -euo pipefail

# --- Configuration ---
readonly WAYBAR_BIN_NAME="waybar"
readonly SPECIAL_WORKSPACE_NAME="special:magic"
# --- End Configuration ---

# --- Script Globals ---
WAYBAR_BIN_PATH=""
_IS_CURRENTLY_ON_SPECIAL="false"
HYPRLAND_SOCKET2=""
# --- End Script Globals ---

# --- Helper Functions ---

find_waybar_binary() {
    WAYBAR_BIN_PATH=$(command -v "$WAYBAR_BIN_NAME" 2>/dev/null) || exit 1
}

check_dependencies() {
    local cmd
    # Added pgrep, pkill, and flock which are actually used by the script
    for cmd in hyprctl jq socat pgrep pkill flock; do
        command -v "$cmd" &>/dev/null || exit 1
    done
}

is_waybar_running() {
    pgrep -x "$WAYBAR_BIN_NAME" &>/dev/null
}

start_waybar() {
    # Don't start if on special workspace
    [[ "$_IS_CURRENTLY_ON_SPECIAL" != "true" ]] || return 0

    # Don't start if already running
    is_waybar_running && return 0

    # Start waybar fully detached (stdin, stdout, stderr redirected, disowned)
    "$WAYBAR_BIN_PATH" </dev/null &>/dev/null &
    disown "$!" 2>/dev/null || true
}

kill_waybar() {
    # Directly attempt to kill; avoids TOCTOU race condition
    # Suppress errors if process doesn't exist
    pkill -x "$WAYBAR_BIN_NAME" 2>/dev/null || true
}

find_hyprland_socket() {
    # Validate XDG_RUNTIME_DIR is set and non-empty
    [[ -n "${XDG_RUNTIME_DIR:-}" ]] || return 1

    local hyprctl_output sig

    hyprctl_output=$(hyprctl instances -j 2>/dev/null) || return 1
    [[ -n "$hyprctl_output" ]] || return 1

    # Use -e to make jq exit with error if result is null/empty
    sig=$(jq -re '.[0].instance // empty' <<< "$hyprctl_output" 2>/dev/null) || return 1

    HYPRLAND_SOCKET2="${XDG_RUNTIME_DIR}/hypr/${sig}/.socket2.sock"

    [[ -S "$HYPRLAND_SOCKET2" ]] || return 1
    return 0
}

update_waybar_visibility() {
    local hypr_output ws_name fullscreen_state jq_result

    # Try to get active window info (contains workspace and fullscreen state)
    if hypr_output=$(hyprctl -j activewindow 2>/dev/null); then
        # Check if we got a valid window (not empty object)
        if [[ -n "$hypr_output" && "$hypr_output" != "{}" && "$hypr_output" != "null" ]]; then
            # Parse both values in one jq call using tab-separated output
            if jq_result=$(jq -re '[(.workspace.name // ""), (.fullscreen // 0)] | @tsv' <<< "$hypr_output" 2>/dev/null); then
                IFS=$'\t' read -r ws_name fullscreen_state <<< "$jq_result"

                # Check for special workspace
                if [[ "$ws_name" == "$SPECIAL_WORKSPACE_NAME" ]]; then
                    _IS_CURRENTLY_ON_SPECIAL="true"
                    kill_waybar
                    return 0
                fi

                _IS_CURRENTLY_ON_SPECIAL="false"

                # Check fullscreen state (1 = fullscreen, 2 = maximized/fake fullscreen)
                case "$fullscreen_state" in
                    1|2)
                        kill_waybar
                        ;;
                    *)
                        start_waybar
                        ;;
                esac
                return 0
            fi
        fi
    fi

    # Fallback: activewindow failed or returned empty, check activeworkspace
    if hypr_output=$(hyprctl -j activeworkspace 2>/dev/null) && [[ -n "$hypr_output" ]]; then
        ws_name=$(jq -r '.name // ""' <<< "$hypr_output" 2>/dev/null) || ws_name=""

        if [[ "$ws_name" == "$SPECIAL_WORKSPACE_NAME" ]]; then
            _IS_CURRENTLY_ON_SPECIAL="true"
            kill_waybar
            return 0
        fi
    fi

    # Default: not on special workspace, show waybar
    _IS_CURRENTLY_ON_SPECIAL="false"
    start_waybar
}

# --- Main Logic ---
main() {
    find_waybar_binary
    check_dependencies
    find_hyprland_socket || exit 1

    # Set initial state based on current Hyprland state
    update_waybar_visibility

    # Declare loop variables outside loop to avoid repeated local declarations
    local event_line event_type event_payload special_name

    # Use process substitution instead of pipe to keep loop in main shell context
    # This ensures _IS_CURRENTLY_ON_SPECIAL modifications persist correctly
    while IFS= read -r event_line || [[ -n "$event_line" ]]; do
        # Parse event type and payload (format: "event_type>>payload")
        event_type="${event_line%%>>*}"
        event_payload="${event_line#*>>}"

        case "$event_type" in
            activespecial)
                # Payload format: "NAME,MONITOR" or ",MONITOR" (if no special active)
                special_name="${event_payload%%,*}"

                if [[ "$special_name" == "$SPECIAL_WORKSPACE_NAME" ]]; then
                    # Entering our target special workspace
                    if [[ "$_IS_CURRENTLY_ON_SPECIAL" == "false" ]]; then
                        _IS_CURRENTLY_ON_SPECIAL="true"
                        kill_waybar
                    fi
                elif [[ "$_IS_CURRENTLY_ON_SPECIAL" == "true" ]]; then
                    # Leaving our special workspace, re-evaluate state
                    update_waybar_visibility
                fi
                ;;

            workspace)
                # Workspace changed - always re-evaluate
                update_waybar_visibility
                ;;

            fullscreen|activewindow)
                # Only re-evaluate if not on special workspace
                [[ "$_IS_CURRENTLY_ON_SPECIAL" == "false" ]] && update_waybar_visibility
                ;;
        esac
    done < <(socat -u "UNIX-CONNECT:${HYPRLAND_SOCKET2}" - 2>/dev/null)
}

# --- Locking Mechanism ---
# Use XDG_RUNTIME_DIR for lock file (more appropriate than /tmp for session-specific locks)
readonly LOCK_DIR="${XDG_RUNTIME_DIR:-/tmp}"
readonly LOCK_FILE="${LOCK_DIR}/waybar_visibility_manager.lock"

cleanup() {
    # Remove lock file on exit
    rm -f "$LOCK_FILE" 2>/dev/null || true
}

setup_lock() {
    # Ensure lock directory exists
    [[ -d "$LOCK_DIR" ]] || exit 1

    # Create/truncate lock file
    : > "$LOCK_FILE" 2>/dev/null || exit 1

    # Open lock file on file descriptor 9
    exec 9>"$LOCK_FILE"

    # Try to acquire exclusive lock (non-blocking)
    # flock is atomic and handles stale locks correctly
    if ! flock -n 9; then
        # Another instance is already running
        exit 1
    fi

    # Write PID to lock file for debugging purposes
    echo $$ >&9

    # Set up cleanup trap for various exit scenarios
    trap cleanup EXIT INT TERM HUP
}

# --- Entry Point ---
setup_lock
main
