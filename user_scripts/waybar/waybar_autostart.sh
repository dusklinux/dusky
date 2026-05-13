#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Description: Robustly restarts Waybar for Hyprland/UWSM sessions.
#              Uses systemd-run to spawn from a clean user environment,
#              avoiding XDG_ACTIVATION_TOKEN inheritance issues.
# Author: dusk
# -----------------------------------------------------------------------------

set -euo pipefail

# --- Constants ---
readonly APP_NAME="waybar"
readonly TIMEOUT_SEC=5
readonly PROXY_SCRIPT="$HOME/user_scripts/hypr/hypr_lua_proxy.py"
readonly PROXY_SIG="lua_proxy"

# --- Terminal-Aware Colors (stderr detection) ---
if [[ -t 2 ]]; then
    readonly C_RED=$'\033[0;31m'
    readonly C_GREEN=$'\033[0;32m'
    readonly C_BLUE=$'\033[0;34m'
    readonly C_RESET=$'\033[0m'
else
    readonly C_RED=''
    readonly C_GREEN=''
    readonly C_BLUE=''
    readonly C_RESET=''
fi

# --- Logging Functions (Strictly to stderr) ---
# Redirecting logs to stderr keeps stdout clean for piping if needed.
log_info()    { printf '%s[INFO]%s %s\n' "${C_BLUE}" "${C_RESET}" "$*" >&2; }
log_success() { printf '%s[OK]%s %s\n' "${C_GREEN}" "${C_RESET}" "$*" >&2; }
log_err()     { printf '%s[ERROR]%s %s\n' "${C_RED}" "${C_RESET}" "$*" >&2; }

# --- Fallback Strategy ---
# Note: As discovered, this method may not cure the "workspace inheritance"
# bug, but it ensures Waybar launches if systemd is broken.
launch_fallback() {
    log_info "Attempting fallback launch (setsid)..."
    (
        unset XDG_ACTIVATION_TOKEN DESKTOP_STARTUP_ID
        export HYPRLAND_INSTANCE_SIGNATURE="${PROXY_SIG}"
        setsid "${APP_NAME}" "$@" </dev/null >/dev/null 2>&1 &
    )
    log_success "${APP_NAME} launched (fallback mode)."
}

# --- Preflight Checks ---
(( EUID != 0 )) || { log_err "This script must NOT be run as root."; exit 1; }
command -v "${APP_NAME}" >/dev/null 2>&1 || { log_err "${APP_NAME} binary not found."; exit 1; }
[[ -d ${XDG_RUNTIME_DIR:-} ]] || { log_err "XDG_RUNTIME_DIR is not set or invalid."; exit 1; }

readonly LOCK_FILE="${XDG_RUNTIME_DIR}/${APP_NAME}_manager.lock"

# --- Concurrency Lock ---
# FD 9 is used to hold the lock until the script exits.
exec 9>"${LOCK_FILE}"
flock -n 9 || { log_err "Another instance is running. Exiting."; exit 1; }

# --- Process Management ---
log_info "Managing ${APP_NAME} instances..."

if pgrep -x "${APP_NAME}" >/dev/null 2>&1; then
    log_info "Stopping existing instances..."
    pkill -x "${APP_NAME}" >/dev/null 2>&1 || true

    # Poll for termination (Bash C-style loop)
    for (( i = 0; i < TIMEOUT_SEC * 10; i++ )); do
        pgrep -x "${APP_NAME}" >/dev/null 2>&1 || break
        sleep 0.1
    done

    # Force kill if still resistant
    if pgrep -x "${APP_NAME}" >/dev/null 2>&1; then
        log_err "Process hung. Sending SIGKILL..."
        pkill -9 -x "${APP_NAME}" >/dev/null 2>&1 || true
        sleep 0.2
    fi
    log_success "Cleanup complete."
else
    log_info "No running instance found."
fi

# --- Lua IPC Proxy ---
# Command socket: Python proxy translates old-style "dispatch workspace N"
# into valid Lua expressions before forwarding to the real Hyprland socket.
# Event socket: socat pure pass-through (C-level, zero Python overhead).
PROXY_CMD_SOCK="${XDG_RUNTIME_DIR}/hypr/${PROXY_SIG}/.socket.sock"
PROXY_EVT_SOCK="${XDG_RUNTIME_DIR}/hypr/${PROXY_SIG}/.socket2.sock"

# Retry hyprctl until the real instance is discoverable (race-safe)
REAL_DIR=""
for (( i = 0; i < 50; i++ )); do
    _sig=$(hyprctl instances -j 2>/dev/null \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["instance"])' 2>/dev/null || true)
    if [[ -n "${_sig}" && -S "${XDG_RUNTIME_DIR}/hypr/${_sig}/.socket2.sock" ]]; then
        REAL_DIR="${XDG_RUNTIME_DIR}/hypr/${_sig}"
        break
    fi
    sleep 0.1
done
[[ -n "${REAL_DIR}" ]] || { log_err "Could not locate real Hyprland socket — proxy aborted."; exit 1; }

log_info "Restarting Lua IPC proxy..."

# Stop and fully clear any previous proxy units before re-creating them
systemctl --user stop hypr-lua-proxy hypr-socat-proxy 2>/dev/null || true
systemctl --user reset-failed hypr-lua-proxy hypr-socat-proxy 2>/dev/null || true

# Ensure proxy directory exists before socat tries to bind into it
mkdir -p "${XDG_RUNTIME_DIR}/hypr/lua_proxy"

# Command socket — Python in its own persistent systemd unit
systemd-run --user --quiet --unit="hypr-lua-proxy" \
    -- python3 "${PROXY_SCRIPT}" 2>/dev/null || true

# Event socket — socat in its own persistent systemd unit
systemd-run --user --quiet --unit="hypr-socat-proxy" \
    -- socat \
        "UNIX-LISTEN:${PROXY_EVT_SOCK},fork,unlink-early" \
        "UNIX-CONNECT:${REAL_DIR}/.socket2.sock" \
    2>/dev/null || true

for (( i = 0; i < 30; i++ )); do
    [[ -S "${PROXY_CMD_SOCK}" && -S "${PROXY_EVT_SOCK}" ]] && break
    sleep 0.1
done
[[ -S "${PROXY_CMD_SOCK}" && -S "${PROXY_EVT_SOCK}" ]] \
    && log_success "Lua proxy sockets ready." \
    || log_err "Lua proxy socket(s) not ready — workspace clicks may be broken."

# --- Launch Sequence ---
log_info "Starting ${APP_NAME}..."

if command -v systemd-run >/dev/null 2>&1; then
    # Optimization: Use Bash 5.0+ $EPOCHSECONDS instead of forking $(date)
    # Security: Add $$ (PID) to unit name to prevent collision on rapid re-runs
    unit_name="${APP_NAME}-mgr-${EPOCHSECONDS}-$$"

    # '--' separates options from the command to prevent flag injection
    if systemd-run --user --quiet --unit="${unit_name}" \
            --setenv=HYPRLAND_INSTANCE_SIGNATURE="${PROXY_SIG}" \
            -- "${APP_NAME}" "$@" >/dev/null 2>&1; then
        log_success "${APP_NAME} launched via systemd unit: ${unit_name}"
    else
        log_err "systemd-run failed; attempting fallback."
        launch_fallback "$@"
    fi
else
    launch_fallback "$@"
fi
