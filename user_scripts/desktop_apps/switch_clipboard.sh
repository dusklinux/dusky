#!/usr/bin/env bash
# ==============================================================================
# Script: switch_clipboard.sh
# Purpose: Toggle between Terminal and Rofi clipboard managers for Hyprland.
#          - Terminal mode: Uncomments/adds the custom clipboard keybind.
#          - Rofi mode: Comments out the custom keybind (restores default).
# Config:  ~/.config/hypr/edit_here/source/keybinds.conf
# System:  Arch Linux (Hyprland/Wayland)
# flags for auto --terminal --rofi
# ==============================================================================

set -euo pipefail

# ------------------------------------------------------------------------------
# Configuration Constants
# ------------------------------------------------------------------------------
readonly CONFIG_DIR="${HOME}/.config/hypr/edit_here/source"
readonly CONFIG_FILE="${CONFIG_DIR}/keybinds.conf"
readonly MARKER_START='# -- TERMINAL-CLIPBOARD-START --'
readonly MARKER_END='# -- TERMINAL-CLIPBOARD-END --'
readonly BIND_SIGNATURE='toggle_terminal_clipboard.sh'

# ------------------------------------------------------------------------------
# Terminal Colors (Conditional on TTY)
# ------------------------------------------------------------------------------
if [[ -t 1 ]]; then
    readonly C_RED=$'\e[31m' C_GREEN=$'\e[32m' C_YELLOW=$'\e[33m'
    readonly C_BLUE=$'\e[34m' C_BOLD=$'\e[1m' C_RESET=$'\e[0m'
else
    readonly C_RED='' C_GREEN='' C_YELLOW='' C_BLUE='' C_BOLD='' C_RESET=''
fi

# ------------------------------------------------------------------------------
# Logging Functions
# ------------------------------------------------------------------------------
die()     { printf '%s[FATAL]%s %s\n' "${C_RED}" "${C_RESET}" "$1" >&2; exit "${2:-1}"; }
info()    { printf '%s[INFO]%s %s\n' "${C_BLUE}" "${C_RESET}" "$1"; }
success() { printf '%s[OK]%s %s\n' "${C_GREEN}" "${C_RESET}" "$1"; }
warn()    { printf '%s[WARN]%s %s\n' "${C_YELLOW}" "${C_RESET}" "$1"; }

# ------------------------------------------------------------------------------
# Helper: Generate the keybind configuration block
# ------------------------------------------------------------------------------
generate_terminal_block() {
    printf '%s\n' \
        "${MARKER_START}" \
        'unbind = $mainMod, V' \
        'bindd = $mainMod, V, Clipboard History, exec, $scripts/clipboard/toggle_terminal_clipboard.sh uwsm-app -- kitty --class terminal_clipboard.sh -e "$scripts/clipboard/terminal_clipboard.sh"' \
        "${MARKER_END}"
}

# ------------------------------------------------------------------------------
# Helper: Check if config file contains our marker block (Pure Bash)
# Returns: 0 if markers exist, 1 otherwise.
# ------------------------------------------------------------------------------
has_marker_block() {
    [[ -f "${CONFIG_FILE}" ]] || return 1
    local content
    content=$(<"${CONFIG_FILE}")
    [[ "${content}" == *"${MARKER_START}"* ]]
}

# ------------------------------------------------------------------------------
# Core: Detect current clipboard state
# Returns: 0 if Terminal mode is ACTIVE, 1 if Rofi/default.
# ------------------------------------------------------------------------------
get_clipboard_state() {
    info "Inspecting configuration state at: ${CONFIG_FILE}"
    
    [[ -f "${CONFIG_FILE}" ]] || return 1

    local content
    content=$(<"${CONFIG_FILE}")

    # No markers → default Rofi mode
    [[ "${content}" == *"${MARKER_START}"* ]] || return 1

    # Scan for an uncommented bind signature within the marker block
    local in_block=0 line ltrimmed
    while IFS= read -r line || [[ -n "${line}" ]]; do
        if [[ "${line}" == *"${MARKER_START}"* ]]; then
            in_block=1
            continue
        elif [[ "${line}" == *"${MARKER_END}"* ]]; then
            break  # Past the block, stop scanning
        fi

        if (( in_block )); then
            # Left-trim: strip leading whitespace (Bash idiom: remove longest match of non-space from start)
            ltrimmed="${line#"${line%%[![:space:]]*}"}"
            
            # Active if line contains signature AND is not commented
            if [[ "${ltrimmed}" == *"${BIND_SIGNATURE}"* && "${ltrimmed}" != '#'* ]]; then
                info "Detected active Terminal keybinding."
                return 0  # Terminal mode is ACTIVE
            fi
        fi
    done <<< "${content}"

    info "Markers found, but configuration is commented out (Rofi active)."
    return 1  # Markers exist but all lines commented → Rofi mode
}

# ------------------------------------------------------------------------------
# Core: Modify configuration block (Atomic)
# Usage: modify_config_block <comment|uncomment>
# ------------------------------------------------------------------------------
modify_config_block() {
    local action="$1"
    info "Starting atomic file modification: ${action}..."

    local tmpfile
    tmpfile=$(mktemp) || die "Failed to create temporary file"
    trap 'rm -f -- "${tmpfile}"' EXIT

    local -a lines=()
    mapfile -t lines < "${CONFIG_FILE}"

    local -a output=()
    local in_block=0 line ltrimmed

    for line in "${lines[@]}"; do
        if [[ "${line}" == *"${MARKER_START}"* ]]; then
            in_block=1
            output+=("${line}")
        elif [[ "${line}" == *"${MARKER_END}"* ]]; then
            in_block=0
            output+=("${line}")
        elif (( in_block )); then
            # Left-trim leading whitespace
            ltrimmed="${line#"${line%%[![:space:]]*}"}"

            if [[ "${action}" == "comment" && "${ltrimmed}" != '#'* ]]; then
                # Comment: prefix with "# " (only if not already commented)
                output+=("# ${line}")
            elif [[ "${action}" == "uncomment" && "${ltrimmed}" == '# '* ]]; then
                # Uncomment: remove first occurrence of "# "
                output+=("${line/'# '/}")
            else
                output+=("${line}")
            fi
        else
            output+=("${line}")
        fi
    done

    # Atomic write: temp file → rename
    printf '%s\n' "${output[@]}" > "${tmpfile}"
    mv -f -- "${tmpfile}" "${CONFIG_FILE}"
    info "Successfully updated configuration file."

    trap - EXIT  # Clear cleanup trap on success
}

# ------------------------------------------------------------------------------
# Action: Enable Terminal clipboard mode
# ------------------------------------------------------------------------------
enable_terminal_mode() {
    info "Initiating switch to Terminal Clipboard mode..."

    if has_marker_block; then
        modify_config_block "uncomment"
    else
        info "No existing block found. Appending new configuration..."
        mkdir -p "${CONFIG_DIR}"
        # Append block directly using group redirection (no subshell)
        {
            printf '\n'
            generate_terminal_block
        } >> "${CONFIG_FILE}"
        info "Configuration appended to ${CONFIG_FILE}"
    fi

    success "Terminal Clipboard enabled."
}

# ------------------------------------------------------------------------------
# Action: Enable Rofi clipboard mode
# ------------------------------------------------------------------------------
enable_rofi_mode() {
    info "Initiating switch to Rofi Clipboard mode..."

    if has_marker_block; then
        modify_config_block "comment"
        success "Rofi Clipboard enabled (Terminal config commented out)."
    else
        success "System is already using Rofi Clipboard (no Terminal config present)."
    fi
}

# ------------------------------------------------------------------------------
# Interactive Menu
# ------------------------------------------------------------------------------
show_menu() {
    local current_state="$1"
    local state_label

    if [[ "${current_state}" == "terminal" ]]; then
        state_label="${C_GREEN}Terminal${C_RESET}"
    else
        state_label="${C_YELLOW}Rofi${C_RESET}"
    fi

    printf '\n%s%sClipboard Manager Selection%s\n' "${C_BOLD}" "${C_BLUE}" "${C_RESET}"
    printf 'Current: %s\n\n' "${state_label}"
    printf '  1) %sTerminal Clipboard%s  (with image previews)\n' "${C_GREEN}" "${C_RESET}"
    printf '  2) %sRofi Clipboard%s      (standard text list)\n' "${C_YELLOW}" "${C_RESET}"
    printf '\n%sChoice [1/2]:%s ' "${C_BOLD}" "${C_RESET}"
}

# ------------------------------------------------------------------------------
# Usage
# ------------------------------------------------------------------------------
usage() {
    printf 'Usage: %s [OPTIONS]\n\n' "${0##*/}"
    printf 'Options:\n'
    printf '  --terminal   Enable Terminal clipboard mode\n'
    printf '  --rofi       Enable Rofi clipboard mode (default)\n'
    printf '  -h, --help   Show this help message\n'
}

# ------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------
main() {
    # Guard: do not run as root
    (( EUID != 0 )) || die "Do not run as root. This modifies user configuration."

    local mode=""

    # Argument parsing
    while (( $# > 0 )); do
        case "$1" in
            --terminal) mode="terminal" ;;
            --rofi)     mode="rofi" ;;
            -h|--help)  usage; exit 0 ;;
            -*)         die "Unknown option: $1" ;;
            *)          die "Unexpected argument: $1" ;;
        esac
        shift
    done

    # Interactive mode if no CLI flags provided
    if [[ -z "${mode}" ]]; then
        local current_state="rofi"
        if get_clipboard_state; then
            current_state="terminal"
        fi

        show_menu "${current_state}"

        local choice=""
        read -r choice || true

        case "${choice}" in
            1) mode="terminal" ;;
            2) mode="rofi" ;;
            *) die "Invalid selection: '${choice:-<empty>}'" ;;
        esac
        printf '\n'
    fi

    # Execute
    case "${mode}" in
        terminal) enable_terminal_mode ;;
        rofi)     enable_rofi_mode ;;
        *)        die "Internal error: invalid mode '${mode}'" ;;
    esac
}

main "$@"
