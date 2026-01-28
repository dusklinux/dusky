#!/usr/bin/env bash
# ==============================================================================
# Script Name: fix_theme_dir.sh
# Description: Enforces 'active_theme' directory state.
#              - Migrates legacy 'active' -> 'active_theme'.
#              - Resolves conflicts (Dark + Light) by attempting to move Light.
#              - If Move fails, WARNS but CONTINUES to ensure Dark is activated.
# Environment: Arch Linux / Hyprland / UWSM
# ==============================================================================

# ------------------------------------------------------------------------------
# 1. Strict Mode & Configuration
# ------------------------------------------------------------------------------
set -euo pipefail
IFS=$'\n\t'

# Absolute Paths for Safety
readonly BASE_DIR="${HOME}/Pictures/wallpapers"
readonly PARENT_DIR="${HOME}/Pictures"

# ANSI Colors
readonly C_GREEN=$'\033[0;32m'
readonly C_BLUE=$'\033[0;34m'
readonly C_YELLOW=$'\033[0;33m'
readonly C_RED=$'\033[0;31m'
readonly C_RESET=$'\033[0m'

# ------------------------------------------------------------------------------
# 2. Helper Functions
# ------------------------------------------------------------------------------
log_info()  { printf "%s[INFO]%s  %s\n" "${C_BLUE}" "${C_RESET}" "$1"; }
log_ok()    { printf "%s[OK]%s    %s\n" "${C_GREEN}" "${C_RESET}" "$1"; }
log_warn()  { printf "%s[WARN]%s  %s\n" "${C_YELLOW}" "${C_RESET}" "$1"; }
log_err()   { printf "%s[ERR]%s   %s\n" "${C_RED}" "${C_RESET}" "$1" >&2; exit 1; }

# ------------------------------------------------------------------------------
# 3. Main Logic
# ------------------------------------------------------------------------------
main() {
    # 3a. Privilege & Sanity Check
    if [[ "${EUID}" -eq 0 ]]; then
        log_err "Do not run as root. Run as user to manage ${HOME}."
    fi

    if [[ ! -d "${BASE_DIR}" ]]; then
        log_err "Base directory not found: ${BASE_DIR}"
    fi

    # Define State Targets
    local dir_dark="${BASE_DIR}/dark"
    local dir_light="${BASE_DIR}/light"
    local dir_target="${BASE_DIR}/active_theme"
    local dir_legacy="${BASE_DIR}/active"

    # --------------------------------------------------------------------------
    # Phase 0: Legacy Migration (active -> active_theme)
    # --------------------------------------------------------------------------
    if [[ -d "${dir_legacy}" ]]; then
        if [[ -d "${dir_target}" ]]; then
            log_warn "Both 'active' and 'active_theme' exist. Cannot migrate automatically."
            log_warn "Using existing 'active_theme' and ignoring 'active'."
        else
            log_info "Legacy directory 'active' detected. Renaming to 'active_theme'..."
            mv "${dir_legacy}" "${dir_target}"
            log_ok "Migration complete."
        fi
    fi

    # --------------------------------------------------------------------------
    # Phase 1: Conflict Resolution
    # Logic: If BOTH exist, try to move 'light' out.
    # CRITICAL FIX: If move fails, do NOT exit. Log WARN and proceed.
    # --------------------------------------------------------------------------
    if [[ -d "${dir_dark}" && -d "${dir_light}" ]]; then
        log_info "Conflict detected: Both 'dark' and 'light' exist."

        if [[ -d "${PARENT_DIR}/light" ]]; then
            log_warn "Destination conflict: '${PARENT_DIR}/light' already exists."
            log_warn "Skipping move of 'light' folder to prevent data loss."
            # We continue execution so 'dark' can still be renamed below.
        else
            mv "${dir_light}" "${PARENT_DIR}/"
            log_ok "Moved 'light' to ${PARENT_DIR}/."
        fi
    fi

    # --------------------------------------------------------------------------
    # Phase 2: Activation
    # Logic: Rename available candidate to 'active_theme'.
    # --------------------------------------------------------------------------
    
    # 2a. Check if target is already set
    if [[ -d "${dir_target}" ]]; then
        # If we have stragglers (e.g. we failed to move light, or dark reappeared)
        if [[ -d "${dir_dark}" ]]; then
             log_warn "Ambiguous state: 'active_theme' is set, but 'dark' also exists."
             exit 0
        fi
        log_ok "System is already in a valid state ('active_theme' exists)."
        exit 0
    fi

    # 2b. Perform Rename
    # Priority is 'dark' (Default standard)
    if [[ -d "${dir_dark}" ]]; then
        mv "${dir_dark}" "${dir_target}"
        log_ok "Activated: Renamed 'dark' to 'active_theme'."
    elif [[ -d "${dir_light}" ]]; then
        mv "${dir_light}" "${dir_target}"
        log_ok "Activated: Renamed 'light' to 'active_theme'."
    else
        log_warn "No candidates ('dark' or 'light') found to activate."
    fi
}

# Execute
main "$@"
