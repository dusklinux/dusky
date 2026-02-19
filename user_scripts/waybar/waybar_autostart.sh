#!/usr/bin/env bash
set -euo pipefail

# ------------------------------------------------------
# Robust Waybar launcher for Hyprland / systemd
# ------------------------------------------------------
readonly APP_NAME="waybar"
readonly TIMEOUT_SEC=5

# State file written by theme_ctl.sh before Matugen runs.
# Presence means Waybar was running before the theme change.
readonly WAYBAR_STATE_FILE="${XDG_RUNTIME_DIR:-/tmp}/waybar_was_running"

# Terminal-aware colors
if [[ -t 2 ]]; then
    readonly C_RED=$'\033[0;31m'
    readonly C_GREEN=$'\033[0;32m'
    readonly C_BLUE=$'\033[0;34m'
    readonly C_RESET=$'\033[0m'
else
    readonly C_RED='' C_GREEN='' C_BLUE='' C_RESET=''
fi

log_info()    { printf '%s[INFO]%s %s\n'    "${C_BLUE}"  "${C_RESET}" "$*" >&2; }
log_success() { printf '%s[OK]%s %s\n'      "${C_GREEN}" "${C_RESET}" "$*" >&2; }
log_err()     { printf '%s[ERROR]%s %s\n'   "${C_RED}"   "${C_RESET}" "$*" >&2; }

# ============================================================
# STATE FILE GUARD
# When called with "theme-change", respect the state file:
#   - State file present  → Waybar was running → proceed to launch
#   - State file absent   → Waybar was hidden  → exit cleanly
# When called without "theme-change" (e.g. from a keybind or
# session startup), always proceed regardless.
# ============================================================
if [[ "${1:-}" == "theme-change" ]]; then
    if [[ ! -f "$WAYBAR_STATE_FILE" ]]; then
        log_info "theme-change mode: Waybar was hidden before theme change, skipping launch."
        exit 0
    fi
    log_info "theme-change mode: Waybar was running before theme change, proceeding."
    rm -f "$WAYBAR_STATE_FILE"
    shift
fi

# ============================================================
# PREFLIGHT
# ============================================================
(( EUID != 0 )) || { log_err "Do NOT run as root"; exit 1; }
command -v "${APP_NAME}" >/dev/null 2>&1 || { log_err "${APP_NAME} not found"; exit 1; }
[[ -d ${XDG_RUNTIME_DIR:-} ]]            || { log_err "XDG_RUNTIME_DIR invalid"; exit 1; }

readonly LOCK_FILE="${XDG_RUNTIME_DIR}/${APP_NAME}_manager.lock"
exec 9>"${LOCK_FILE}"
flock -n 9 || { log_err "Another instance running"; exit 1; }

# ============================================================
# MANAGE EXISTING INSTANCES
# ============================================================
log_info "Managing ${APP_NAME} instances..."

if pgrep -x "${APP_NAME}" >/dev/null 2>&1; then
    log_info "Stopping existing instances..."
    pkill -x "${APP_NAME}" >/dev/null 2>&1 || true

    for (( i=0; i < TIMEOUT_SEC*10; i++ )); do
        pgrep -x "${APP_NAME}" >/dev/null 2>&1 || break
        sleep 0.1
    done

    if pgrep -x "${APP_NAME}" >/dev/null 2>&1; then
        log_err "Hung process, sending SIGKILL..."
        pkill -9 -x "${APP_NAME}" >/dev/null 2>&1 || true
        sleep 0.2
    fi

    log_success "Cleanup complete."
else
    log_info "No running instance found."
fi

# ============================================================
# LAUNCH
# ============================================================
launch_fallback() {
    log_info "Attempting fallback launch (setsid)..."
    (
        unset XDG_ACTIVATION_TOKEN DESKTOP_STARTUP_ID
        setsid "${APP_NAME}" "$@" </dev/null >/dev/null 2>&1 &
    )
    log_success "${APP_NAME} launched (fallback mode)."
}

log_info "Starting ${APP_NAME}..."

if command -v systemd-run >/dev/null 2>&1; then
    unit_name="${APP_NAME}-mgr-${EPOCHSECONDS}-$$"
    if systemd-run --user --quiet --unit="${unit_name}" -- "${APP_NAME}" "$@" >/dev/null 2>&1; then
        log_success "${APP_NAME} launched via systemd unit: ${unit_name}"
    else
        log_err "systemd-run failed; trying fallback..."
        launch_fallback "$@"
    fi
else
    launch_fallback "$@"
fi
