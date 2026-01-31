#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Script: waybar_manager.sh
# Description: Robustly restarts Waybar for Hyprland/UWSM sessions.
#              Prevents race conditions and duplicate instances on hotplug.
#              Transparently passes arguments to the Waybar binary.
# Author: Arch System Architect
# -----------------------------------------------------------------------------

# --- 1. Safety & Environment ---
set -euo pipefail

# Constants
readonly APP_NAME="waybar"
readonly TIMEOUT_SEC=5

# --- 2. Terminal-Aware Colors ---
# Only use colors if stderr is a TTY (interactive terminal). 
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

# --- 3. Logging Functions ---
# Use %s placeholders to prevent printf from interpreting variables as format strings.
log_info()    { printf '%s[INFO]%s %s\n' "${C_BLUE}" "${C_RESET}" "$*"; }
log_success() { printf '%s[OK]%s %s\n' "${C_GREEN}" "${C_RESET}" "$*"; }
log_err()     { printf '%s[ERROR]%s %s\n' "${C_RED}" "${C_RESET}" "$*" >&2; }

# --- 4. Preflight Checks ---
# Use arithmetic context (( )) for numeric comparison.
(( EUID != 0 )) || { log_err "This script must NOT be run as root."; exit 1; }

# Check for binary presence
command -v "${APP_NAME}" &>/dev/null || { log_err "${APP_NAME} binary not found in PATH."; exit 1; }

# Critical Wayland Check
# We prefer explicit failure over a blind 'mkdir -p' in /run/user.
[[ -d "${XDG_RUNTIME_DIR:-}" ]] || { log_err "XDG_RUNTIME_DIR is missing or invalid."; exit 1; }

readonly LOCK_FILE="${XDG_RUNTIME_DIR}/${APP_NAME}_manager.lock"

# --- 5. Concurrency Locking ---
# Open file descriptor 9 for the lock file.
exec 9>"${LOCK_FILE}"

# Attempt to acquire an exclusive non-blocking lock.
flock -n 9 || { log_err "Another instance is running. Exiting."; exit 1; }

# --- 6. Process Management ---
log_info "Managing ${APP_NAME} instances..."

if pgrep -x "${APP_NAME}" >/dev/null; then
    log_info "Stopping existing instances..."
    
    # Polite Kill (SIGTERM)
    pkill -x "${APP_NAME}" 2>/dev/null || true
    
    # Wait loop (C-style for loop avoids 'set -e' trap on arithmetic evaluation)
    for ((i=0; i<TIMEOUT_SEC*10; i++)); do
        pgrep -x "${APP_NAME}" >/dev/null || break
        sleep 0.1
    done

    # Force kill if still alive
    if pgrep -x "${APP_NAME}" >/dev/null; then
        log_err "Process hung. Sending SIGKILL..."
        pkill -9 -x "${APP_NAME}" 2>/dev/null || true
        sleep 0.2
    fi
    
    log_success "Cleanup complete."
else
    log_info "No running instance found."
fi

# --- 7. Launch ---
log_info "Starting ${APP_NAME}..."

"${APP_NAME}" "$@" >/dev/null 2>&1 9>&- &
disown

log_success "Waybar detached successfully."
exit 0
