#!/usr/bin/env bash

# Enforce strict execution mode: fail on error, fail on unset var, fail on pipe errors
set -euo pipefail
IFS=$'\n\t'

# Prevent execution as root (AUR helpers enforce this strictly)
if (( EUID == 0 )); then
    printf "\e[38;5;196m[ERROR]\e[0m This script must not be run as root. Execute as a standard user.\n" >&2
    exit 1
fi

# Terminal colors for structured, readable logging
declare -r C_RESET=$'\e[0m'
declare -r C_INFO=$'\e[38;5;111m'
declare -r C_SUCCESS=$'\e[38;5;114m'
declare -r C_WARN=$'\e[38;5;214m'
declare -r C_ERR=$'\e[38;5;196m'

# Initialize state integers for pure arithmetic evaluation
declare -i OPT_PACMAN=0
declare -i OPT_AUR=0
declare AUR_HELPER=""
declare SUDO_PID=0
declare -r MAIN_PID=$$

# Logging functions
log_info()    { printf "%b[INFO]%b %s\n" "$C_INFO" "$C_RESET" "$1"; }
log_success() { printf "%b[SUCCESS]%b %s\n" "$C_SUCCESS" "$C_RESET" "$1"; }
log_warn()    { printf "%b[WARN]%b %s\n" "$C_WARN" "$C_RESET" "$1" >&2; }
log_err()     { printf "%b[ERROR]%b %s\n" "$C_ERR" "$C_RESET" "$1" >&2; }
die()         { log_err "$1"; exit 1; }

# Helper discovery using fast path hashing (zero subshell overhead)
find_aur_helper() {
    if hash paru 2>/dev/null; then
        AUR_HELPER="paru"
    elif hash yay 2>/dev/null; then
        AUR_HELPER="yay"
    fi
}

# Display usage
show_help() {
    cat << EOF
Usage: ${0##*/} [OPTIONS]

A robust system updater for Pacman and AUR packages.

Options:
  -p, --pacman    Update standard repository packages only (pacman)
  -a, --aur       Update AUR packages only (prefers paru, fallback yay)
  -A, --all       Update both repository and AUR packages
  -h, --help      Display this help message and exit
EOF
}

# Parse arguments using native arithmetic evaluation
if (( $# == 0 )); then
    show_help
    exit 1
fi

while (( $# > 0 )); do
    case "$1" in
        -p|--pacman) OPT_PACMAN=1 ;;
        -a|--aur)    OPT_AUR=1 ;;
        -A|--all)    OPT_PACMAN=1; OPT_AUR=1 ;;
        -h|--help)   show_help; exit 0 ;;
        *)           die "Invalid argument: $1. Use --help for usage." ;;
    esac
    shift
done

# Pre-flight checks
find_aur_helper

if (( OPT_AUR == 1 )) && [[ -z "$AUR_HELPER" ]]; then
    die "AUR update requested, but neither 'paru' nor 'yay' is installed in your PATH."
fi

# ---------------------------------------------------------
# Sudo Keep-Alive Lifecycle Management & IPC
# ---------------------------------------------------------

# Trap signal from the keep-alive process to handle silent sudo expiration
trap 'die "Update pipeline aborted: Sudo credentials expired unexpectedly."' USR1

keep_sudo_alive() {
    local sleep_pid=0
    
    # Isolate signal handling to cleanly terminate the sleep child process
    trap '(( sleep_pid > 0 )) && kill "$sleep_pid" 2>/dev/null || true; exit 0' TERM
    
    # Validate credentials strictly via internal logic (zero fork/exec binary overhead)
    while sudo -nv 2>/dev/null; do
        sleep 60 &
        sleep_pid=$!
        wait "$sleep_pid" || true
        sleep_pid=0
    done
    
    # Loop exited: Sudo ticket was invalidated. Escalate to parent PID.
    kill -USR1 "$MAIN_PID" 2>/dev/null || true
}

# Cleanup hook bound to script exit
cleanup() {
    if (( SUDO_PID > 0 )); then
        # Send TERM to the keep-alive subshell, triggering its internal cleanup trap
        kill -TERM "$SUDO_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

# Acquire privileges and start keep-alive
if (( OPT_PACMAN == 1 || OPT_AUR == 1 )); then
    log_info "Acquiring sudo privileges..."
    sudo -v || die "Sudo authentication failed or was cancelled."
    
    keep_sudo_alive &
    SUDO_PID=$!
fi

# ---------------------------------------------------------
# Execution Logic
# ---------------------------------------------------------
log_info "Initiating system update sequence..."

if (( OPT_PACMAN == 1 && OPT_AUR == 1 )); then
    log_info "Executing unified system and AUR update using: $AUR_HELPER"
    "$AUR_HELPER" -Syu

elif (( OPT_AUR == 1 )); then
    log_info "Executing strictly AUR update using: $AUR_HELPER"
    # -Sua restricts targets explicitly to the AUR
    "$AUR_HELPER" -Sua

elif (( OPT_PACMAN == 1 )); then
    log_info "Executing strictly core system update using: pacman"
    sudo pacman -Syu
fi

log_success "Update sequence completed successfully."
