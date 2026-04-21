#!/usr/bin/env bash
#
# simple_terminator.sh
#
# A lightweight, non-interactive utility to terminate processes and stop services.
# Optimized for Arch Linux / Hyprland (run with sudo).
#
set -euo pipefail

# --- CONSTANTS ---
readonly STOP_TIMEOUT=10
readonly PROCESS_WAIT_ATTEMPTS=10
readonly PROCESS_WAIT_INTERVAL=0.1

# --- COLORS (ANSI) ---
readonly RED=$'\033[1;31m'
readonly GREEN=$'\033[1;32m'
readonly YELLOW=$'\033[1;33m'
readonly BLUE=$'\033[1;34m'
readonly GRAY=$'\033[0;37m'
readonly RESET=$'\033[0m'

# --- CONFIGURATION ---

# 1. Processes (Raw Binaries to pkill)
readonly -a TARGET_PROCESSES=(
#    "hyprsunset"
    "waybar"
    "blueman-manager"
)

# 2. System Services (Requires Root)
readonly -a TARGET_SYSTEM_SERVICES=(
    "firewalld"
    "vsftpd"
    "waydroid-container"
    "logrotate.timer"
    "sshd"
)

# 3. User Services (Requires User Context)
readonly -a TARGET_USER_SERVICES=(
    "battery_notify"
    "blueman-applet"
    "hypridle"
    "gvfs-daemon"
    "gvfs-metadata"
    "network_meter"
)

# --- GLOBALS ---
FAILURE_COUNT=0
REAL_USER=""
REAL_UID=""
USER_RUNTIME_DIR=""
USER_DBUS_ADDRESS=""

# --- TRAP ---
# Ensure terminal colors are reset even if the script crashes or is interrupted
cleanup_exit() {
    printf '%s\n' "${RESET}"
}
trap cleanup_exit EXIT INT TERM

# --- FUNCTIONS ---

die() {
    printf '%sError: %s%s\n' "${RED}" "$1" "${RESET}" >&2
    exit 1
}

warn() {
    printf '%sWarning: %s%s\n' "${YELLOW}" "$1" "${RESET}" >&2
}

check_root() {
    if [[ ${EUID} -ne 0 ]]; then
        die "This script must be run as root (sudo)."
    fi
}

check_dependencies() {
    local -a missing=()
    local cmd
    
    for cmd in pgrep pkill systemctl id timeout env; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done
    
    if [[ ${#missing[@]} -gt 0 ]]; then
        die "Missing required commands: ${missing[*]}"
    fi
}

detect_real_user() {
    if [[ -n "${SUDO_USER:-}" ]]; then
        REAL_USER="$SUDO_USER"
        REAL_UID=$(id -u "$SUDO_USER") || die "Could not determine UID for user '$SUDO_USER'"
    else
        warn "Script not run via sudo. User services may not stop correctly."
        REAL_USER="root"
        REAL_UID=0
    fi
    
    USER_RUNTIME_DIR="/run/user/${REAL_UID}"
    USER_DBUS_ADDRESS="unix:path=${USER_RUNTIME_DIR}/bus"
}

print_status() {
    local status="$1"
    local name="$2"
    local extra="${3:-}"
    
    case "$status" in
        success)
            printf '[%s OK %s] Stopped: %s%s\n' "${GREEN}" "${RESET}" "${name}" "${extra:+ ($extra)}"
            ;;
        skip)
            printf '[%sSKIP%s] Not running: %s%s\n' "${GRAY}" "${RESET}" "${name}" "${extra:+ ($extra)}"
            ;;
        fail)
            printf '[%sFAIL%s] Could not stop: %s%s\n' "${RED}" "${RESET}" "${name}" "${extra:+ ($extra)}"
            # CRITICAL FIX: ((count++)) returns 1 if the value is 0. 
            # We must use '|| true' to prevent 'set -e' from killing the script.
            ((FAILURE_COUNT++)) || true
            ;;
    esac
}

stop_process() {
    local name="$1"
    local i
    
    # Check if process exists
    if ! pgrep -x "$name" &>/dev/null; then
        print_status "skip" "$name"
        return 0
    fi
    
    # Try graceful termination (SIGTERM)
    # pkill returns 1 if no process matched; strict mode requires '|| true' here
    # just in case the process died between the pgrep check and now.
    pkill -x "$name" 2>/dev/null || true
    
    # Wait for process to terminate
    for ((i = 0; i < PROCESS_WAIT_ATTEMPTS; i++)); do
        if ! pgrep -x "$name" &>/dev/null; then
            print_status "success" "$name" "SIGTERM"
            return 0
        fi
        sleep "$PROCESS_WAIT_INTERVAL"
    done
    
    # Escalate to SIGKILL
    pkill -9 -x "$name" 2>/dev/null || true
    sleep 0.3
    
    # Final check
    if ! pgrep -x "$name" &>/dev/null; then
        print_status "success" "$name" "SIGKILL"
    else
        print_status "fail" "$name"
    fi
}

stop_system_service() {
    local name="$1"
    
    # Check if service exists and is active
    if ! systemctl is-active --quiet "$name" 2>/dev/null; then
        print_status "skip" "$name"
        return 0
    fi
    
    # Attempt to stop with timeout
    if timeout "$STOP_TIMEOUT" systemctl stop "$name" 2>/dev/null; then
        # Verify stopped
        if ! systemctl is-active --quiet "$name" 2>/dev/null; then
            print_status "success" "$name"
            return 0
        fi
    fi
    
    print_status "fail" "$name"
}

run_as_user() {
    # Helper to run systemctl --user with proper environment
    # Use 'env' explicitly to set variables, protected by '--'
    sudo -u "$REAL_USER" -- env \
        XDG_RUNTIME_DIR="$USER_RUNTIME_DIR" \
        DBUS_SESSION_BUS_ADDRESS="$USER_DBUS_ADDRESS" \
        "$@"
}

stop_user_service() {
    local name="$1"
    
    # Skip if running as actual root (no user session)
    if [[ "$REAL_USER" == "root" ]]; then
        print_status "skip" "$name" "no user session"
        return 0
    fi
    
    # Check if user runtime directory exists
    if [[ ! -d "$USER_RUNTIME_DIR" ]]; then
        print_status "skip" "$name" "no runtime dir"
        return 0
    fi
    
    # Check if service is active
    if ! run_as_user systemctl --user is-active --quiet "$name" 2>/dev/null; then
        print_status "skip" "$name"
        return 0
    fi
    
    # Attempt to stop with timeout
    if run_as_user timeout "$STOP_TIMEOUT" systemctl --user stop "$name" 2>/dev/null; then
        # Verify stopped
        if ! run_as_user systemctl --user is-active --quiet "$name" 2>/dev/null; then
            print_status "success" "$name"
            return 0
        fi
    fi
    
    print_status "fail" "$name"
}

draw_line() {
    local width="$1"
    local line
    # Pure Bash optimization: create string of spaces, replace with dashes
    printf -v line '%*s' "$width" ''
    printf '%s\n' "${line// /-}"
}

print_header() {
    local width=44
    local title="Performance Terminator"
    local user_info
    
    # Using printf -v to format string into variable
    printf -v user_info "User: %s (UID: %s)" "$REAL_USER" "$REAL_UID"
    
    echo ""
    draw_line "$width"
    printf " %-*s\n" $((width - 2)) "$title"
    printf " %-*s\n" $((width - 2)) "$user_info"
    draw_line "$width"
}

print_footer() {
    local width=44
    
    echo ""
    draw_line "$width"
    if [[ $FAILURE_COUNT -eq 0 ]]; then
        printf " %sCleanup complete. All operations successful.%s\n" "${GREEN}" "${RESET}"
    else
        printf " %sCleanup complete with %d failure(s).%s\n" "${YELLOW}" "$FAILURE_COUNT" "${RESET}"
    fi
    draw_line "$width"
}

# --- MAIN ---

main() {
    local item
    
    check_root
    check_dependencies
    detect_real_user
    
    print_header
    
    printf "\n%s:: Processes%s\n" "${BLUE}" "${RESET}"
    for item in "${TARGET_PROCESSES[@]}"; do
        stop_process "$item"
    done
    
    printf "\n%s:: System Services%s\n" "${BLUE}" "${RESET}"
    for item in "${TARGET_SYSTEM_SERVICES[@]}"; do
        stop_system_service "$item"
    done
    
    printf "\n%s:: User Services%s\n" "${BLUE}" "${RESET}"
    for item in "${TARGET_USER_SERVICES[@]}"; do
        stop_user_service "$item"
    done
    
    print_footer
    
    # Exit with failure code if there were errors
    [[ $FAILURE_COUNT -eq 0 ]]
}

main "$@"
