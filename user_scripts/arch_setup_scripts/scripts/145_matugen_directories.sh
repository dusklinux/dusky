#!/usr/bin/env bash
# Create configuration directories for Matugen for theming
# -----------------------------------------------------------------------------
# Description:  Bootstrap configuration directories for Hyprland/UWSM environment
# Target:       Arch Linux / Bash 5+
# Standards:    Strict Mode, No External Logs, Idempotent
# -----------------------------------------------------------------------------

# 1. Strict Mode & Safety
# -e: Exit immediately on error
# -u: Exit on unset variables
# -o pipefail: Capture errors in pipe chains
set -euo pipefail

# 2. Visual Feedback (ANSI Colors)
readonly C_RESET=$'\033[0m'
readonly C_GREEN=$'\033[1;32m'
readonly C_BLUE=$'\033[1;34m'
readonly C_RED=$'\033[1;31m'
readonly C_GRAY=$'\033[0;90m'

# 3. Utility Functions (Modern Bash)
log_info() { printf "${C_BLUE}[INFO]${C_RESET} %s\n" "$1"; }
log_success() { printf "${C_GREEN}[OK]${C_RESET}   %s\n" "$1"; }
log_err() { printf "${C_RED}[ERR]${C_RESET}  %s\n" "$1" >&2; }

# Trap for cleanup or final status
cleanup() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        log_err "Script interrupted or failed with code $exit_code."
    fi
    # No temporary files to remove, but placeholder for extensibility
}
trap cleanup EXIT

# 4. Main Logic
main() {
    # Using an array for better management and scalability
    # We use explicit $HOME expansion to avoid tilde quirks in quotes
    local -a target_dirs=(
        "${HOME}/.config/gtk-4.0"
        "${HOME}/.config/btop/themes"
        "${HOME}/.config/opencode/themes"
        "${HOME}/.cache/wal"
    )

    log_info "Initializing environment directories..."

    for dir in "${target_dirs[@]}"; do
        # Check existence first for cleaner output logic (idempotency)
        if [[ -d "$dir" ]]; then
            printf "${C_GRAY}[SKIP]${C_RESET} Directory exists: %s\n" "$dir"
        else
            # mkdir -p is atomic and handles parent creation
            if mkdir -p "$dir"; then
                log_success "Created: $dir"
            else
                log_err "Failed to create: $dir"
                return 1
            fi
        fi
    done

    # UWSM Note: Since we are just creating dirs, no systemd-notify is strictly needed here,
    # but ensuring these exist prevents race conditions for apps launched by UWSM later.
    log_info "Directory initialization complete."
}

# Execute
main "$@"
