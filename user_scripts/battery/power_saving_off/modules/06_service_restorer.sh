#!/usr/bin/env bash
#
# 07_process_restore.sh (User Mode)
#
# A lightweight, non-interactive utility to START processes and services.
# Optimized for Arch Linux / Hyprland / UWSM.
#
# USAGE: Run as NORMAL USER (dusk). Do NOT run with sudo.
#        The script will ask for sudo password only for system services.
#

# --- STRICT MODE ---
set -euo pipefail

# --- CONSTANTS ---
readonly START_TIMEOUT=10
readonly PROCESS_START_WAIT=2.0

# --- COLORS (ANSI) ---
readonly RED=$'\033[1;31m'
readonly GREEN=$'\033[1;32m'
readonly YELLOW=$'\033[1;33m'
readonly BLUE=$'\033[1;34m'
readonly GRAY=$'\033[0;37m'
readonly RESET=$'\033[0m'

# --- CONFIGURATION ---

# 1. User Processes (GUI/Wayland apps)
#    - Runs natively as YOU. 
#    - No environment hacking needed.
readonly -a TARGET_PROCESSES=(
#    "hyprsunset"
    "waybar"
    "blueman-manager"
)

# 2. User Services (systemd --user)
#    - Runs natively as YOU.
readonly -a TARGET_USER_SERVICES=(
    "battery_notify"
    "hypridle"
    "gvfs-daemon"
    "gvfs-metadata"
    "network_meter"
)

# 3. System Services (Requires Root)
#    - Will trigger a sudo prompt if needed.
readonly -a TARGET_SYSTEM_SERVICES=(
    "firewalld"
    "vsftpd"
    "waydroid-container"
    "logrotate.timer"
    "sshd"
)

# --- GLOBALS ---
FAILURE_COUNT=0

# --- TRAP ---
cleanup_exit() {
    printf '%s' "${RESET}"
}
trap cleanup_exit EXIT

# --- FUNCTIONS ---

print_status() {
    local status="$1"
    local name="$2"
    local extra="${3:-}"
    local suffix=""

    [[ -n "$extra" ]] && suffix=" (${extra})"

    case "$status" in
        success)
            printf '[%s OK %s] Started: %s%s\n' "${GREEN}" "${RESET}" "${name}" "${suffix}"
            ;;
        skip)
            printf '[%sSKIP%s] Already running: %s%s\n' "${GRAY}" "${RESET}" "${name}" "${suffix}"
            ;;
        fail)
            printf '[%sFAIL%s] Could not start: %s%s\n' "${RED}" "${RESET}" "${name}" "${suffix}"
            FAILURE_COUNT=$((FAILURE_COUNT + 1))
            ;;
    esac
}

draw_line() {
    local width="$1"
    local line
    printf -v line '%*s' "$width" ''
    printf '%s\n' "${line// /-}"
}

print_header() {
    local width=52
    local title="Process Initiator (User Mode)"
    local user_info

    printf -v user_info 'User: %s (UID: %s)' "$USER" "$EUID"

    printf '\n'
    draw_line "$width"
    printf ' %-*s\n' $((width - 2)) "$title"
    printf ' %-*s\n' $((width - 2)) "$user_info"
    draw_line "$width"
}

print_footer() {
    local width=52
    printf '\n'
    draw_line "$width"
    if [[ $FAILURE_COUNT -eq 0 ]]; then
        printf ' %sStartup complete. All operations successful.%s\n' "${GREEN}" "${RESET}"
    else
        printf ' %sStartup complete with %d failure(s).%s\n' "${YELLOW}" "$FAILURE_COUNT" "${RESET}"
    fi
    draw_line "$width"
}

# --- PROCESS STARTERS ---

start_process() {
    local name="$1"

    # Check if process is already running
    if pgrep -x "$name" &>/dev/null; then
        print_status "skip" "$name"
        return 0
    fi

    # Start via UWSM (Native User Context)
    # No sudo needed. It inherits YOUR Wayland socket.
    # FIX: Added 'nohup' to prevent SIGHUP when the terminal closes
    nohup uwsm-app -- "$name" >/dev/null 2>&1 & 
    disown $!

    sleep "$PROCESS_START_WAIT"

    if pgrep -x "$name" &>/dev/null; then
        print_status "success" "$name"
    else
        print_status "fail" "$name" "check ~/.local/share/uwsm/logs"
    fi
}

start_user_service() {
    local name="$1"

    if systemctl --user is-active --quiet "$name" 2>/dev/null; then
        print_status "skip" "$name"
        return 0
    fi

    if timeout "$START_TIMEOUT" systemctl --user start "$name" 2>/dev/null; then
        if systemctl --user is-active --quiet "$name" 2>/dev/null; then
            print_status "success" "$name"
            return 0
        fi
    fi

    print_status "fail" "$name"
}

start_system_service() {
    local name="$1"

    if systemctl is-active --quiet "$name" 2>/dev/null; then
        print_status "skip" "$name"
        return 0
    fi

    # ESCALATION: This is the ONLY place we use sudo.
    # It will use your cached sudo credential or prompt you.
    if sudo timeout "$START_TIMEOUT" systemctl start "$name" 2>/dev/null; then
        if systemctl is-active --quiet "$name" 2>/dev/null; then
            print_status "success" "$name"
            return 0
        fi
    fi

    print_status "fail" "$name" "requires sudo/root"
}

# --- MAIN ---

main() {
    # Guard: Prevent running as root
    if [[ $EUID -eq 0 ]]; then
        printf '%sError: Run this as your NORMAL user, not root (sudo).%s\n' "${RED}" "${RESET}"
        exit 1
    fi

    print_header

    # 1. Validate sudo access upfront (optional, creates cache)
    printf "%s:: Validating sudo permissions...%s\n" "${BLUE}" "${RESET}"
    if ! sudo -v; then
        printf "%sError: Sudo authentication failed.%s\n" "${RED}" "${RESET}"
        exit 1
    fi

    printf '\n%s:: Processes (via uwsm-app)%s\n' "${BLUE}" "${RESET}"
    for item in "${TARGET_PROCESSES[@]}"; do
        start_process "$item"
    done

    printf '\n%s:: User Services%s\n' "${BLUE}" "${RESET}"
    for item in "${TARGET_USER_SERVICES[@]}"; do
        start_user_service "$item"
    done

    printf '\n%s:: System Services (Elevated)%s\n' "${BLUE}" "${RESET}"
    for item in "${TARGET_SYSTEM_SERVICES[@]}"; do
        start_system_service "$item"
    done

    print_footer

    [[ $FAILURE_COUNT -eq 0 ]]
}

main "$@"
