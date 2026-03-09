#!/usr/bin/env bash
# ==============================================================================
# Hyprland Config Integrator - The "Golden" Multi-Line Build (Symlink-Safe)
# Target: Arch Linux (Bash 5.3+) | Wayland/Hyprland Ecosystem
# Architecture: Zero-Corruption Atomic Writes, Pure Bash Parsing, Symlink Safe
# ==============================================================================

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

# ==============================================================================
# EDIT HERE: USER CONFIGURATION
# ==============================================================================
# 1. Define the target file (Symlinks handled automatically).

# 2. Define the exact, literal line or lines (singluar or plural) you want to ensure exist in the file.
# ==============================================================================
# HOW TO ADD LINES:
# ALWAYS wrap each line in SINGLE QUOTES (' ').
#
# WHY?
# 1. It prevents Bash from breaking lines apart at the spaces.
# 2. It prevents Bash from crashing when it sees Hyprland variables (like $mainMod).
#
# GOOD: '$single_window_gap = 10'
# BAD:  $single_window_gap = 10
#
# TIP: if you want to append a comment make sure to have it in single quotes too eg ('# Edit configs here')
# ==============================================================================

readonly TARGET_FILE="${HOME}/.config/hypr/edit_here/source/"

readonly TARGET_LINES=(
# EG:
# '$single_window_gap = 10'
)
# ==============================================================================

# ==============================================================================
# CORE EXECUTION
# ==============================================================================
main() {
    log_info "Evaluating configuration state for ${TARGET_FILE}..."

    # 1. Path Resolution (Symlink / Dotfile Manager Safety)
    local actual_target="${TARGET_FILE}"
    if [[ -L "${TARGET_FILE}" ]]; then
        # -m ensures safe resolution even if the underlying file doesn't exist yet
        actual_target=$(realpath -m "${TARGET_FILE}")
        log_info "Symlink detected. Operating on true target: ${actual_target}"
    fi

    local target_dir="${actual_target%/*}"

    # 2. Zero-Assumption Infrastructure Provisioning
    if [[ ! -d "${target_dir}" ]]; then
        log_info "Configuration directory missing. Provisioning: ${target_dir}"
        mkdir -p "${target_dir}"
    fi

    if [[ ! -f "${actual_target}" ]]; then
        log_info "Target file missing. Initializing empty config..."
        touch "${actual_target}"
    fi

    if [[ ! -w "${actual_target}" ]]; then
        log_err "CRITICAL: Write permission denied for ${actual_target}"
        exit 1
    fi

    # 3. Load File Into Memory (Zero-Subprocess Parsing)
    local existing_lines=()
    # mapfile safely loads the file into an array, stripping newlines (-t)
    mapfile -t existing_lines < "${actual_target}"

    # Pure Bash exact-match helper
    line_exists() {
        local search="$1"
        local l
        for l in "${existing_lines[@]}"; do
            if [[ "$l" == "$search" ]]; then
                return 0
            fi
        done
        return 1
    }

    # 4. Evaluate Idempotency & Build Buffer
    local output_buffer=()
    for target_line in "${TARGET_LINES[@]}"; do
        if ! line_exists "${target_line}"; then
            output_buffer+=("${target_line}")
        fi
    done

    # Fast exit if nothing needs to be added
    if [[ ${#output_buffer[@]} -eq 0 ]]; then
        log_ok "Idempotent. All target lines are already present."
        exit 0
    fi

    log_info "State mismatch. Commencing atomic integration..."

    # 5. Secure Temporary Block Allocation
    local temp_file
    temp_file=$(mktemp "${target_dir}/.hyprland.conf.XXXXXX") || {
        log_err "Failed to allocate temporary file descriptor."
        exit 1
    }

    # 6. Cascading Signal Interception
    trap "[[ -f \"${temp_file}\" ]] && rm -f \"${temp_file}\"" EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM

    # 7. Metadata Cloning
    command cp -pf "${actual_target}" "${temp_file}"

    # 8. Surgical Formatting (Trailing Newline Protection)
    if [[ -s "${temp_file}" ]] && [[ -n "$(tail -c 1 "${temp_file}" | tr -d '\n')" ]]; then
        printf "\n" >> "${temp_file}"
    fi

    # 9. Batch Buffer Flush (Single I/O Operation)
    if ! printf "%s\n" "${output_buffer[@]}" >> "${temp_file}"; then
        log_err "I/O failure during buffer flush."
        exit 1
    fi

    # 10. Targeted VFS Cache Flush
    if ! sync "${temp_file}"; then
        log_err "Kernel rejected physical block sync for ${temp_file}"
        exit 1
    fi

    # 11. The Atomic Rename (Applied to the true target, preserving symlinks)
    if ! command mv -f "${temp_file}" "${actual_target}"; then
        log_err "Atomic swap failed."
        exit 1
    fi

    log_ok "Successfully integrated missing lines via fail-proof atomic write."
}

# Execute Main
main "$@"
