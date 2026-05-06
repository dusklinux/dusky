#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Script: reload_quickpanal.sh
# Purpose: Forcefully manages the Dusky quickpanal lifecycle.
#          1. Snapshots and terminates running instances (SIGTERM -> SIGKILL).
#          2. Resets systemd failure state.
#          3. Starts a clean systemd user service instance.
#          4. Signals the UI to activate via D-Bus.
# Compatibility: Bash 5.3+, Arch Linux, UWSM/Hyprland
# -----------------------------------------------------------------------------

set -euo pipefail

# -----------------------------------------------------------------------------
# SIGNAL TRAP
# -----------------------------------------------------------------------------
trap '' HUP

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
readonly APP_NAME="Dusky quickpanal"
readonly SERVICE_NAME="dusky_quickpanal.service"
readonly PROCESS_PATTERN='dusky_quickpanal\.py'
readonly GUI_SCRIPT_PATH="${HOME}/user_scripts/dusky_system/quickpanal/dusky_quickpanal.py"

# Timing Constants (Seconds)
readonly GRACE_PERIOD_LOOPS=20
readonly GRACE_SLEEP_SEC=0.1
readonly POST_KILL_SETTLE_SEC=0.2
readonly SERVICE_INIT_DELAY_SEC=0.3
readonly DBUS_REGISTRATION_DELAY_SEC=1

readonly SELF_PID=$$

# -----------------------------------------------------------------------------
# Terminal Colors (TTY Detection)
# -----------------------------------------------------------------------------
if [[ -t 1 && -t 2 ]]; then
    readonly C_RED=$'\e[31m' C_GREEN=$'\e[32m' C_YELLOW=$'\e[33m'
    readonly C_BLUE=$'\e[34m' C_BOLD=$'\e[1m' C_RESET=$'\e[0m'
else
    readonly C_RED='' C_GREEN='' C_YELLOW='' C_BLUE='' C_BOLD='' C_RESET=''
fi

log_info() { printf '%s[INFO]%s %s\n' "${C_BLUE}" "${C_RESET}" "$*"; }
log_ok()   { printf '%s[OK]%s %s\n' "${C_GREEN}" "${C_RESET}" "$*"; }
log_warn() { printf '%s[WARN]%s %s\n' "${C_YELLOW}" "${C_RESET}" "$*" >&2; }
log_err()  { printf '%s[ERR]%s %s\n' "${C_RED}" "${C_RESET}" "$*" >&2; }

# -----------------------------------------------------------------------------
# Preflight Checks
# -----------------------------------------------------------------------------
preflight_checks() {
    if ((EUID == 0)); then
        log_err "This script manages a user service. Do not run as root."
        return 1
    fi

    local -a missing=()
    for cmd in pgrep systemctl journalctl python3; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done

    if ((${#missing[@]} > 0)); then
        log_err "Missing required binaries: ${missing[*]}"
        return 1
    fi
}

# -----------------------------------------------------------------------------
# Process Management
# -----------------------------------------------------------------------------
get_target_pids() {
    local pid
    while IFS= read -r pid; do
        if [[ "$pid" =~ ^[0-9]+$ ]] && ((pid != SELF_PID)); then
            printf '%s\n' "$pid"
        fi
    done < <(pgrep -f -- "$PROCESS_PATTERN" 2>/dev/null || true)
}

terminate_processes() {
    (($# > 0)) || return 0
    local -a pids=("$@")
    local pid i all_exited

    log_info "Terminating instances (PIDs: ${pids[*]})..."

    for pid in "${pids[@]}"; do
        kill -TERM -- "$pid" 2>/dev/null || true
    done

    for ((i = 0; i < GRACE_PERIOD_LOOPS; i++)); do
        all_exited=1
        for pid in "${pids[@]}"; do
            if kill -0 -- "$pid" 2>/dev/null; then
                all_exited=0
                break
            fi
        done

        if ((all_exited)); then
            log_ok "Processes terminated gracefully."
            return 0
        fi
        sleep "$GRACE_SLEEP_SEC"
    done

    log_warn "Grace period exceeded. Sending SIGKILL..."
    for pid in "${pids[@]}"; do
        kill -KILL -- "$pid" 2>/dev/null || true
    done
    
    sleep "$POST_KILL_SETTLE_SEC"
    log_ok "Forced termination complete."
}

# -----------------------------------------------------------------------------
# Service Management
# -----------------------------------------------------------------------------
start_and_verify_service() {
    log_info "Starting systemd service: ${C_BOLD}${SERVICE_NAME}${C_RESET}"

    systemctl --user reset-failed -- "$SERVICE_NAME" 2>/dev/null || true

    if ! systemctl --user start -- "$SERVICE_NAME"; then
        log_err "systemctl start failed. Dumping logs:"
        journalctl --user -u "$SERVICE_NAME" -n 15 --no-pager >&2
        return 1
    fi

    sleep "$SERVICE_INIT_DELAY_SEC"

    if ! systemctl --user is-active --quiet -- "$SERVICE_NAME"; then
        log_err "Service started but immediately exited. Dumping logs:"
        journalctl --user -u "$SERVICE_NAME" -n 10 --no-pager >&2
        return 1
    fi

    log_ok "Service is active."
}

# -----------------------------------------------------------------------------
# UI Activation
# -----------------------------------------------------------------------------
activate_ui() {
    if [[ ! -f "$GUI_SCRIPT_PATH" ]]; then
        log_warn "UI script not found at: $GUI_SCRIPT_PATH"
        return 0
    fi

    log_info "Activating UI window via D-Bus..."

    # GTK4 Adw.Application natively handles D-Bus activation.
    # Running it sends the signal to the primary daemon and exits immediately.
    if [[ -x "$GUI_SCRIPT_PATH" ]]; then
        "$GUI_SCRIPT_PATH" >/dev/null 2>&1
    else
        python3 -- "$GUI_SCRIPT_PATH" >/dev/null 2>&1
    fi
}

# -----------------------------------------------------------------------------
# Main Orchestrator
# -----------------------------------------------------------------------------
main() {
    local quiet_mode=0
    
    while (($# > 0)); do
        case "$1" in
            -q|--quiet) quiet_mode=1; shift ;;
            *) shift ;;
        esac
    done

    preflight_checks || return 1

    log_info "Initiating restart for ${C_BOLD}${APP_NAME}${C_RESET}..."

    local -a target_pids
    mapfile -t target_pids < <(get_target_pids)

    if ((${#target_pids[@]} > 0)); then
        terminate_processes "${target_pids[@]}"
    else
        log_info "No running instances found. Environment is clean."
    fi

    start_and_verify_service || return 1

    log_info "Waiting for DBus registration (${DBUS_REGISTRATION_DELAY_SEC}s)..."
    sleep "$DBUS_REGISTRATION_DELAY_SEC"
    
    if (( quiet_mode == 0 )); then
        activate_ui
    else
        log_info "Quiet mode enabled. Skipping UI activation."
    fi

    log_ok "Restart sequence complete."
}

main "$@"
