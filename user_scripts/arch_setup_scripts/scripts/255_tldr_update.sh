#!/usr/bin/env bash
# Runs tldr update

# --- 1. Strict Mode & Safety ---
set -euo pipefail
IFS=$'\n\t'

# --- 2. Configuration & Visuals ---
# ANSI-C quoting: escape sequences are interpreted at assignment time,
# so colors work with printf %s, echo, heredocs — everywhere.
readonly C_RESET=$'\033[0m'
readonly C_GREEN=$'\033[1;32m'
readonly C_BLUE=$'\033[1;34m'
readonly C_RED=$'\033[1;31m'

# Application binary
readonly BIN_TLDR="tldr"

# Connectivity pre-check (tealdeer fetches from GitHub)
readonly CONN_HOST="github.com"
readonly CONN_PORT=443
readonly CONN_TIMEOUT=5

# --- 3. Helper Functions ---

# Logging — colors passed via %s so the format string is a safe literal
log_info()    { printf '%s[INFO]%s %s\n' "$C_BLUE"  "$C_RESET" "$1"; }
log_success() { printf '%s[OK]%s   %s\n' "$C_GREEN" "$C_RESET" "$1"; }
log_error()   { printf '%s[ERR]%s  %s\n' "$C_RED"   "$C_RESET" "$1" >&2; }

# Cleanup (triggered on EXIT) — reset colors so a mid-script abort
# never leaves the terminal styled
cleanup() {
    printf '%s' "$C_RESET"
}
trap cleanup EXIT

# --- 4. Pre-flight Checks ---

check_dependencies() {
    if ! command -v "$BIN_TLDR" >/dev/null 2>&1; then
        log_error "Command '${BIN_TLDR}' not found."
        log_error "Install it via pacman: sudo pacman -S tealdeer"
        exit 1
    fi
}

check_connectivity() {
    log_info "Verifying connectivity to ${CONN_HOST}:${CONN_PORT}..."
    # Bash /dev/tcp opens a TCP socket — the no-op ':' is the cheapest way
    # to test the connection.  'timeout' guards against hangs.
    if ! timeout "$CONN_TIMEOUT" bash -c ": >/dev/tcp/${CONN_HOST}/${CONN_PORT}" 2>/dev/null; then
        log_error "Cannot reach ${CONN_HOST}:${CONN_PORT}. Update aborted."
        exit 1
    fi
}

# --- 5. Main Execution ---

main() {
    # ${USER} may be unset in cron / minimal environments; fall back safely
    log_info "Initializing tldr cache update for user: ${USER:-$(id -un)}"

    check_dependencies
    check_connectivity

    log_info "Executing update..."

    if "$BIN_TLDR" --update; then
        printf '\n'
        log_success "TLDR cache updated successfully."
    else
        printf '\n'
        log_error "Failed to update TLDR cache."
        exit 1
    fi
}

main "$@"
