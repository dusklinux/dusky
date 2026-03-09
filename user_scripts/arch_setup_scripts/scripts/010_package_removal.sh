#!/usr/bin/env bash
# package removal pacman and aur
#              Supports Repo (pacman) and AUR (yay/paru).
#              Safe execution: Strict literal matching to avoid virtual providers.
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
  power-profiles-daemon
  fluent-icon-theme-git
)

# AUR Packages (yay/paru, no sudo)
readonly -a AUR_TARGETS=(
)

# ==============================================================================
# CONSTANTS & STYLING
# ==============================================================================

readonly SCRIPT_NAME="${0##*/}"
readonly SCRIPT_VERSION="2.1.0"

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

# Defaulted to 1 for fully autonomous execution without the -y flag
declare -gi AUTO_CONFIRM=1
declare -gi EXIT_CODE=0
declare -g  AUR_HELPER=''
declare -gi INTERRUPTED=0

# ==============================================================================
# SIGNAL HANDLING
# ==============================================================================

cleanup() {
    local -ri code=$?
    (( INTERRUPTED )) && return 0
    if (( code != 0 )); then
        printf '\n%s[!] Script exited with code: %d%s\n' \
            "${RED}" "$code" "${RESET}" >&2
    fi
    return 0
}
trap cleanup EXIT

handle_interrupt() {
    INTERRUPTED=1
    printf '\n%s[!] Interrupted by signal.%s\n' "${RED}" "${RESET}" >&2
    exit "$1"
}
trap 'handle_interrupt 130' INT
trap 'handle_interrupt 143' TERM

# ==============================================================================
# ARGUMENT PARSING
# ==============================================================================

show_help() {
    cat <<EOF
${BOLD}${SCRIPT_NAME}${RESET} v${SCRIPT_VERSION} — Arch Package Removal Tool

${BOLD}USAGE:${RESET}
    ${SCRIPT_NAME} [OPTIONS]

${BOLD}OPTIONS:${RESET}
    -y, --auto      Skip confirmation prompts (Default behavior)
    -h, --help      Show this help message
    -V, --version   Show version information
EOF
}

show_version() {
    printf '%s v%s\n' "$SCRIPT_NAME" "$SCRIPT_VERSION"
}

parse_args() {
    while (( $# )); do
        case $1 in
            -y|--auto) AUTO_CONFIRM=1; shift ;;
            -h|--help) show_help; exit 0 ;;
            -V|--version) show_version; exit 0 ;;
            --) shift; break ;;
            -?*) die "Unknown option: $1" ;;
            *) die "Unexpected argument: $1" ;;
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

# Filters input array to strictly installed literal packages.
filter_installed() {
    local -n _filter_in=$1
    local -n _filter_out=$2
    _filter_out=()

    (( ${#_filter_in[@]} )) || return 0

    local pkg
    local -a resolved_pkgs
    for pkg in "${_filter_in[@]}"; do
        [[ -n $pkg ]] || continue

        # Query local DB. Will return providers if virtual package is queried.
        mapfile -t resolved_pkgs < <(pacman -Qq -- "$pkg" 2>/dev/null || true)

        # Enforce strict literal matching to bypass virtual provider traps
        local actual_pkg
        local found_exact=0
        for actual_pkg in "${resolved_pkgs[@]}"; do
            if [[ "$actual_pkg" == "$pkg" ]]; then
                _filter_out+=("$pkg")
                found_exact=1
                break
            fi
        done

        if (( ! found_exact )); then
            log_warn "Skipping '${CYAN}${pkg}${RESET}': exact package not installed."
        fi
    done
    return 0
}

# returns 0 if package is NOT required by any installed package (safe to remove),
# returns 1 if package is required (protected).
is_required_by_anything() {
    local -r pkg="$1"
    local req
    # "Required By" field exists in pacman -Qi output.
    # if it is "None", it's not required by installed packages.
    req="$(pacman -Qi -- "$pkg" 2>/dev/null | awk -F': ' '/^Required By/ {print $2; exit}')"
    [[ -n "$req" && "$req" != "None" ]]
}

# ==============================================================================
# PACKAGE REMOVAL
# ==============================================================================

process_removal() {
    local -r label=$1
    local -r pkg_cmd=$2
    local -r targets_name=$3
    local -ri use_sudo=${4:-0}

    local -a active_targets=()
    filter_installed "$targets_name" active_targets

    # skip packages that are required by other installed packages
    local -a removable_targets=()
    local pkg
    for pkg in "${active_targets[@]}"; do
        if is_required_by_anything "$pkg"; then
            local reqby
            reqby="$(pacman -Qi -- "$pkg" 2>/dev/null | awk -F': ' '/^Required By/ {print $2; exit}')"
            log_warn "Skipping '${CYAN}${pkg}${RESET}': required by installed package(s): ${BOLD}${reqby}${RESET}"
            continue
        fi
        removable_targets+=("$pkg")
    done

    if (( ${#removable_targets[@]} == 0 )); then
        log_info "No ${label} packages require removal."
        return 0
    fi

    local -a cmd=()
    (( use_sudo )) && cmd+=(sudo)
    cmd+=("$pkg_cmd" -Rns)
    (( AUTO_CONFIRM )) && cmd+=(--noconfirm)
    cmd+=(-- "${removable_targets[@]}")

    log_info "Removing ${BOLD}${#removable_targets[@]}${RESET} ${label} package(s):"
    printf '         %s%s%s\n' "${CYAN}" "${removable_targets[*]}" "${RESET}"

    if "${cmd[@]}"; then
        log_ok "${label} package removal completed."
    else
        local -ri cmd_exit=$?
        log_err "Failed to remove some ${label} packages (exit code: ${cmd_exit})."
        EXIT_CODE=1
    fi

    return 0
}

# ==============================================================================
# MAIN EXECUTION
# ==============================================================================

main() {
    parse_args "$@"
    check_environment

    if (( AUTO_CONFIRM )); then
        log_info "Mode: ${YELLOW}Autonomous (--noconfirm)${RESET}"
    fi

    local -ri total_targets=$(( ${#REPO_TARGETS[@]} + ${#AUR_TARGETS[@]} ))
    if (( total_targets == 0 )); then
        log_warn "No packages configured for removal."
        return 0
    fi

    printf '%s\n' "${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

    if (( ${#REPO_TARGETS[@]} )); then
        process_removal "Repo" "pacman" REPO_TARGETS 1
    fi

    if [[ -n $AUR_HELPER ]] && (( ${#AUR_TARGETS[@]} )); then
        process_removal "AUR" "$AUR_HELPER" AUR_TARGETS 0
    fi

    printf '%s\n' "${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

    if (( EXIT_CODE == 0 )); then
        log_ok "Cleanup completed successfully."
    else
        log_warn "Cleanup completed with errors."
    fi

    return "$EXIT_CODE"
}

main "$@"
