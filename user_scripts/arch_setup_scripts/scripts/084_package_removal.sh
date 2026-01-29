#!/usr/bin/env bash
# package removal pacman and aur
#              Supports Repo (pacman) and AUR (yay/paru).
#              Safe execution: validates installation status before removal.
# System:      Arch Linux / UWSM / Hyprland
# Requires:    Bash 5.0+, pacman, sudo
# Flags:       -Rns = Remove + recursive deps + no config backup
# -----------------------------------------------------------------------------

set -euo pipefail
IFS=$' \t\n'

# ==============================================================================
# CONFIGURATION
# ==============================================================================

# Official Repository Packages (sudo pacman)
readonly -a REPO_TARGETS=(
  dunst
  dolphin
  wofi
  polkit-kde-agent
)

# AUR Packages (yay/paru, no sudo)
readonly -a AUR_TARGETS=(
)

# ==============================================================================
# CONSTANTS & STYLING
# ==============================================================================

readonly SCRIPT_NAME="${0##*/}"
readonly SCRIPT_VERSION="2.0.2"

# Terminal-aware coloring (check both stdout and stderr)
if [[ -t 1 && -t 2 ]]; then
    readonly BOLD=$'\e[1m'    DIM=$'\e[2m'
    readonly RED=$'\e[31m'    GREEN=$'\e[32m'
    readonly YELLOW=$'\e[33m' BLUE=$'\e[34m'
    readonly CYAN=$'\e[36m'   RESET=$'\e[0m'
else
    readonly BOLD='' DIM='' RED='' GREEN='' YELLOW='' BLUE='' CYAN='' RESET=''
fi

# ==============================================================================
# LOGGING
# ==============================================================================

log_info() { printf '%s[INFO]%s  %s\n' "${BLUE}${BOLD}" "${RESET}" "${1:-}"; }
log_ok()   { printf '%s[OK]%s    %s\n' "${GREEN}${BOLD}" "${RESET}" "${1:-}"; }
log_warn() { printf '%s[WARN]%s  %s\n' "${YELLOW}${BOLD}" "${RESET}" "${1:-}" >&2; }
log_err()  { printf '%s[ERROR]%s %s\n' "${RED}${BOLD}" "${RESET}" "${1:-}" >&2; }

die() {
    log_err "${1:-Unknown error}"
    exit "${2:-1}"
}

# ==============================================================================
# STATE
# ==============================================================================

declare -gi AUTO_CONFIRM=0
declare -gi EXIT_CODE=0
declare -g  AUR_HELPER=''
declare -gi INTERRUPTED=0

# ==============================================================================
# SIGNAL HANDLING
# ==============================================================================

cleanup() {
    local -ri code=$?
    # Avoid duplicate messages on interrupt
    (( INTERRUPTED )) && return 0
    if (( code != 0 )); then
        printf '\n%s[!] Script exited with code: %d%s\n' \
            "${RED}" "$code" "${RESET}" >&2
        # Ensure we return the actual exit code
        return "$code"
    fi
    return 0
}
trap cleanup EXIT

handle_interrupt() {
    INTERRUPTED=1
    printf '\n%s[!] Interrupted by signal.%s\n' "${RED}" "${RESET}" >&2
    exit "$1"
}
trap 'handle_interrupt 130' INT    # 128 + 2  (SIGINT)
trap 'handle_interrupt 143' TERM   # 128 + 15 (SIGTERM)

# ==============================================================================
# ARGUMENT PARSING
# ==============================================================================

show_help() {
    cat <<EOF
${BOLD}${SCRIPT_NAME}${RESET} v${SCRIPT_VERSION} — Arch Package Removal Tool

${BOLD}USAGE:${RESET}
    ${SCRIPT_NAME} [OPTIONS]

${BOLD}OPTIONS:${RESET}
    -y, --auto      Skip confirmation prompts (--noconfirm)
    -h, --help      Show this help message
    -V, --version   Show version information

${BOLD}CONFIGURATION:${RESET}
    Edit the ${CYAN}REPO_TARGETS${RESET} and ${CYAN}AUR_TARGETS${RESET} arrays within the script
    to specify packages for removal.

${BOLD}REMOVAL FLAGS:${RESET}
    Uses ${YELLOW}-Rns${RESET}: Remove package, dependencies, and config files (no backup)

${BOLD}EXAMPLES:${RESET}
    ${DIM}# Interactive removal${RESET}
    ${SCRIPT_NAME}

    ${DIM}# Automated removal (CI/scripting)${RESET}
    ${SCRIPT_NAME} --auto
EOF
}

show_version() {
    printf '%s v%s\n' "$SCRIPT_NAME" "$SCRIPT_VERSION"
}

parse_args() {
    while (( $# )); do
        case $1 in
            -y|--auto)
                AUTO_CONFIRM=1
                shift
                ;;
            -h|--help)
                show_help
                exit 0
                ;;
            -V|--version)
                show_version
                exit 0
                ;;
            --)
                shift
                break
                ;;
            -?*)
                die "Unknown option: $1 (use --help for usage)"
                ;;
            *)
                die "Unexpected argument: $1"
                ;;
        esac
    done
}

# ==============================================================================
# ENVIRONMENT VALIDATION
# ==============================================================================

check_bash_version() {
    local -ri major=${BASH_VERSINFO[0]}

    if (( major < 5 )); then
        die "Bash 5.0+ required (current: ${BASH_VERSION})"
    fi
}

check_not_root() {
    if (( EUID == 0 )); then
        log_err "Do NOT run this script as root."
        log_err "  • AUR helpers (yay/paru) must run as a normal user"
        log_err "  • sudo will be invoked for pacman when needed"
        exit 1
    fi
}

check_required_commands() {
    local -a missing=()
    local cmd

    for cmd in pacman sudo; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done

    if (( ${#missing[@]} )); then
        die "Missing required commands: ${missing[*]}"
    fi
}

detect_aur_helper() {
    local helper
    for helper in paru yay; do
        if command -v "$helper" &>/dev/null; then
            AUR_HELPER="$helper"
            return 0
        fi
    done

    # Warn only if AUR targets are configured
    if (( ${#AUR_TARGETS[@]} )); then
        log_warn "No AUR helper (paru/yay) found — AUR targets will be skipped."
    fi
    return 0
}

check_environment() {
    check_bash_version
    check_not_root
    check_required_commands
    detect_aur_helper
}

# ==============================================================================
# PACKAGE FILTERING
# ==============================================================================

# Filters input array to only installed packages
# Uses namerefs for efficient array manipulation
# Arguments:
#   $1 - Name of input array
#   $2 - Name of output array (will be cleared and populated)
filter_installed() {
    local -n _filter_in=$1
    local -n _filter_out=$2

    _filter_out=()

    # Early exit if nothing to process
    (( ${#_filter_in[@]} )) || return 0

    local pkg
    for pkg in "${_filter_in[@]}"; do
        # Skip empty entries (defensive)
        [[ -n $pkg ]] || continue

        # pacman -Qq: quiet query for local installation
        if pacman -Qq -- "$pkg" &>/dev/null; then
            _filter_out+=("$pkg")
        else
            log_warn "Skipping '${CYAN}${pkg}${RESET}': not installed"
        fi
    done
    return 0
}

# ==============================================================================
# PACKAGE REMOVAL
# ==============================================================================

# Processes removal for a category of packages
# Arguments:
#   $1 - Label (e.g., "Repo", "AUR")
#   $2 - Package manager command (pacman, paru, yay)
#   $3 - Name of target array
#   $4 - Use sudo flag (1=yes, 0=no)
process_removal() {
    local -r label=$1
    local -r pkg_cmd=$2
    local -r targets_name=$3
    local -ri use_sudo=${4:-0}

    local -a active_targets=()

    # Filter to installed packages only
    filter_installed "$targets_name" active_targets

    # Nothing to do?
    if (( ${#active_targets[@]} == 0 )); then
        log_info "No ${label} packages require removal."
        return 0
    fi

    # Build command array for safe execution
    local -a cmd=()
    (( use_sudo )) && cmd+=(sudo)
    cmd+=("$pkg_cmd" -Rns)
    (( AUTO_CONFIRM )) && cmd+=(--noconfirm)
    cmd+=(-- "${active_targets[@]}")

    # Display what we're about to do
    log_info "Removing ${BOLD}${#active_targets[@]}${RESET} ${label} package(s):"
    printf '         %s%s%s\n' "${CYAN}" "${active_targets[*]}" "${RESET}"

    # Execute removal with error handling
    local -i cmd_exit=0
    "${cmd[@]}" || cmd_exit=$?
    
    if (( cmd_exit == 0 )); then
        log_ok "${label} package removal completed."
        return 0
    else
        # Distinguish between different error scenarios
        case $cmd_exit in
            1)
                log_err "Failed to remove some ${label} packages (general failure)."
                ;;
            2)
                log_err "Invalid usage or syntax error in ${label} removal command."
                ;;
            *)
                log_err "Failed to remove some ${label} packages (exit code: ${cmd_exit})."
                ;;
        esac
        EXIT_CODE=1
        return "$cmd_exit"
    fi
}

# ==============================================================================
# MAIN EXECUTION
# ==============================================================================

main() {
    parse_args "$@"
    check_environment

    # Display mode indicator
    if (( AUTO_CONFIRM )); then
        log_info "Mode: ${YELLOW}Non-interactive (--noconfirm)${RESET}"
    fi

    # Validate configuration
    local -ri total_targets=$(( ${#REPO_TARGETS[@]} + ${#AUR_TARGETS[@]} ))
    if (( total_targets == 0 )); then
        log_warn "No packages configured for removal."
        log_info "Edit REPO_TARGETS and/or AUR_TARGETS arrays in the script."
        return 0
    fi

    printf '%s\n' "${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

    # Process official repository packages
    if (( ${#REPO_TARGETS[@]} )); then
        process_removal "Repo" "pacman" REPO_TARGETS 1 || {
            log_warn "Repo package removal encountered errors but continuing..."
        }
    fi

    # Process AUR packages (warning already issued in detect_aur_helper if missing)
    if [[ -n $AUR_HELPER ]] && (( ${#AUR_TARGETS[@]} )); then
        process_removal "AUR" "$AUR_HELPER" AUR_TARGETS 0 || {
            log_warn "AUR package removal encountered errors but continuing..."
        }
    fi

    printf '%s\n' "${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

    # Final status
    if (( EXIT_CODE == 0 )); then
        log_ok "Cleanup completed successfully."
    else
        log_warn "Cleanup completed with errors (exit code: $EXIT_CODE)."
    fi

    return "$EXIT_CODE"
}

main "$@"
