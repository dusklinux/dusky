#!/usr/bin/env bash
# ==============================================================================
# Hyprland Config Integrator - The "Golden" Build
# Target: Arch Linux (Bash 5.3+) | Wayland/Hyprland Ecosystem
# Architecture: Zero-Corruption Atomic Writes, Surgical Fsync, Strict Trapping
# ==============================================================================

# --- STRICT EXECUTION ENVIRONMENT ---
# -e: Halt immediately on pipeline failure
# -u: Treat unset variables as fatal errors
# -o pipefail: Catch masked errors inside pipes
set -euo pipefail

# --- ANSI TERMINAL CONSTANTS ---
readonly C_BOLD=$'\033[1m'
readonly C_BLUE=$'\033[34m'
readonly C_GREEN=$'\033[32m'
readonly C_RED=$'\033[31m'
readonly C_RESET=$'\033[0m'

log_info() { printf "%s[INFO]%s %s\n" "${C_BLUE}${C_BOLD}" "${C_RESET}" "$*"; }
log_ok()   { printf "%s[OK]%s %s\n" "${C_GREEN}${C_BOLD}" "${C_RESET}" "$*"; }
log_err()  { printf "%s[ERROR]%s %s\n" "${C_RED}${C_BOLD}" "${C_RESET}" "$*" >&2; }

# --- TARGET STATE DEFINITION ---
readonly TARGET_FILE="${HOME}/.config/hypr/edit_here/hyprland.conf"
readonly TARGET_LINE="source = ~/.config/hypr/edit_here/source/workspace_rules.conf"

# ==============================================================================
# CORE EXECUTION
# ==============================================================================
main() {
    log_info "Evaluating Hyprland configuration state..."

    # Pure bash parameter expansion (Zero forks)
    local target_dir="${TARGET_FILE%/*}"

    # 1. Zero-Assumption Infrastructure Provisioning
    if [[ ! -d "${target_dir}" ]]; then
        log_info "Configuration directory missing. Provisioning: ${target_dir}"
        mkdir -p "${target_dir}"
    fi

    if [[ ! -f "${TARGET_FILE}" ]]; then
        log_info "Target file missing. Initializing empty config..."
        touch "${TARGET_FILE}"
    fi

    if [[ ! -w "${TARGET_FILE}" ]]; then
        log_err "CRITICAL: Write permission denied for ${TARGET_FILE}"
        exit 1
    fi

    # 2. Strict Exact-Match Idempotency Check
    # 'grep -qxF' is heavily optimized in C for literal string matching.
    if grep -qxF "${TARGET_LINE}" "${TARGET_FILE}"; then
        log_ok "Idempotent. The workspace rules source line is already present."
        exit 0
    fi

    log_info "State mismatch. Commencing atomic integration..."

    # 3. Secure Temporary Block Allocation
    local temp_file
    temp_file=$(mktemp "${target_dir}/.hyprland.conf.XXXXXX") || {
        log_err "Failed to allocate temporary file descriptor."
        exit 1
    }

    # 4. Cascading Signal Interception
    # FIXED: Double quotes force immediate variable expansion before the local scope is destroyed
    trap "[[ -f \"${temp_file}\" ]] && rm -f \"${temp_file}\"" EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM

    # 5. Metadata Cloning (Bypassing User Aliases)
    # The '-p' flag perfectly duplicates ownership, mode (permissions), and 
    # timestamps before we modify a single byte of data.
    command cp -pf "${TARGET_FILE}" "${temp_file}"

    # 6. Surgical Append Logic (Trailing Newline Protection)
    # ultra-fast check: If the last character isn't a newline, safely pad it first.
    if [[ -s "${temp_file}" ]] && [[ -n "$(tail -c 1 "${temp_file}" | tr -d '\n')" ]]; then
        printf "\n%s\n" "${TARGET_LINE}" >> "${temp_file}"
    else
        printf "%s\n" "${TARGET_LINE}" >> "${temp_file}"
    fi

    # 7. Targeted VFS Cache Flush
    # Forces only this exact file's data from RAM to the physical block device.
    # Eliminates the risk of a corrupted file on a hard kernel panic/power loss.
    if ! sync "${temp_file}"; then
        log_err "Kernel rejected physical block sync for ${temp_file}"
        exit 1
    fi

    # 8. The Atomic Rename
    # This is a single atomic 'rename()' syscall. The file is swapped instantly.
    if ! command mv -f "${temp_file}" "${TARGET_FILE}"; then
        log_err "Atomic swap failed."
        exit 1
    fi

    log_ok "Successfully integrated workspace rules via fail-proof atomic write."
}

# Execute Main
main "$@"
