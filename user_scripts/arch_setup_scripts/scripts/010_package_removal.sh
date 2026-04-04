#!/usr/bin/env bash
# package removal pacman and aur
#              Supports Repo (pacman) and AUR (natively tracked).
#              Intelligent Execution: Evaluates co-dependencies natively to 
#              allow batch removals without blocking.
# System:      Arch Linux / UWSM / Hyprland
# Requires:    Bash 5.0+, pacman, sudo
# Flags:       -Rns = Remove + recursive deps + no config backup
# -----------------------------------------------------------------------------

set -euo pipefail
IFS=$' \t\n'

# ==============================================================================
# CONFIGURATION
# ==============================================================================

# Official Repository Packages
readonly -a REPO_TARGETS=(
  dunst
  dolphin
  wofi
  polkit-kde-agent
  power-profiles-daemon
  fluent-icon-theme-git
  swww
  papirus-folders-git
  papirus-icon-theme-git
)

# AUR Packages 
# (Processed seamlessly with Repo packages as they share the local pacman DB)
readonly -a AUR_TARGETS=(
)

# ==============================================================================
# CONSTANTS & STYLING
# ==============================================================================

readonly SCRIPT_NAME="${0##*/}"
readonly SCRIPT_VERSION="3.0.1"

if [[ -t 1 && -t 2 ]]; then
    readonly BOLD=$'\e[1m'    DIM=$'\e[2m'
    readonly RED=$'\e[31m'    GREEN=$'\e[32m'
    readonly YELLOW=$'\e[33m' BLUE=$'\e[34m'
    readonly CYAN=$'\e[36m'   RESET=$'\e[0m'
else
    readonly BOLD='' DIM='' RED='' GREEN='' YELLOW='' BLUE='' CYAN='' RESET=''
fi

# ==============================================================================
# LOGGING & STATE
# ==============================================================================

log_info() { printf '%s[INFO]%s  %s\n' "${BLUE}${BOLD}" "${RESET}" "${1:-}"; }
log_ok()   { printf '%s[OK]%s    %s\n' "${GREEN}${BOLD}" "${RESET}" "${1:-}"; }
log_warn() { printf '%s[WARN]%s  %s\n' "${YELLOW}${BOLD}" "${RESET}" "${1:-}" >&2; }
log_err()  { printf '%s[ERROR]%s %s\n' "${RED}${BOLD}" "${RESET}" "${1:-}" >&2; }

die() {
    log_err "${1:-Unknown error}"
    exit "${2:-1}"
}

declare -gi AUTO_CONFIRM=1
declare -gi EXIT_CODE=0
declare -gi INTERRUPTED=0

# ==============================================================================
# SIGNAL HANDLING
# ==============================================================================

cleanup() {
    local -ri code=$?
    
    if (( INTERRUPTED )); then
        return 0
    fi
    
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
# ARGUMENT PARSING & ENVIRONMENT VALIDATION
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

check_environment() {
    local -ri major=${BASH_VERSINFO[0]}
    
    if (( major < 5 )); then
        die "Bash 5.0+ required (current: ${BASH_VERSION})"
    fi
    
    if (( EUID == 0 )); then
        die "Do NOT run this script as root."
    fi

    local cmd
    local -a missing=()
    
    for cmd in pacman sudo; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    
    if (( ${#missing[@]} > 0 )); then
        die "Missing required commands: ${missing[*]}"
    fi
    
    # Explicit return 0 guarantees a clean exit code for set -e
    return 0
}

# ==============================================================================
# PACKAGE FILTERING & RESOLUTION
# ==============================================================================

# Filters input array strictly to installed literal packages
filter_installed() {
    local -n _filter_in=$1
    local -n _filter_out=$2
    _filter_out=()

    if (( ${#_filter_in[@]} == 0 )); then
        return 0
    fi

    local pkg actual_pkg found_exact
    local -a resolved_pkgs

    for pkg in "${_filter_in[@]}"; do
        [[ -z $pkg ]] && continue

        # Query local DB. Allows exact match isolation against virtual providers.
        mapfile -t resolved_pkgs < <(pacman -Qq -- "$pkg" 2>/dev/null || true)

        found_exact=0
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

# Iterative cascade algorithm to resolve co-dependencies safely.
# Eliminates packages required by software NOT in the removal array.
resolve_safe_removals() {
    local -n _input_pkgs=$1
    local -n _safe_pkgs=$2
    _safe_pkgs=("${_input_pkgs[@]}")

    if (( ${#_safe_pkgs[@]} == 0 )); then
        return 0
    fi

    local changed=1
    while (( changed )); do
        changed=0
        local pkg
        local -a current_check=("${_safe_pkgs[@]}")

        for pkg in "${current_check[@]}"; do
            local req_str
            # Force C locale to guarantee standard English output for reliable parsing
            req_str=$(LC_ALL=C pacman -Qi -- "$pkg" 2>/dev/null | grep '^Required By' | cut -d':' -f2- || true)

            local -a req_pkgs
            read -ra req_pkgs <<< "$req_str"

            # If required by something other than "None"
            if (( ${#req_pkgs[@]} > 0 )) && [[ "${req_pkgs[0]}" != "None" ]]; then
                local req_pkg is_safe=1

                for req_pkg in "${req_pkgs[@]}"; do
                    local check_pkg found=0
                    # Check if the demanding package is also scheduled for removal
                    for check_pkg in "${_safe_pkgs[@]}"; do
                        if [[ "$check_pkg" == "$req_pkg" ]]; then
                            found=1
                            break
                        fi
                    done

                    # If required by an installed package NOT in our removal list, it's unsafe
                    if (( ! found )); then
                        log_warn "Skipping '${CYAN}${pkg}${RESET}': required by retained package: ${BOLD}${req_pkg}${RESET}"
                        is_safe=0
                        break
                    fi
                done

                # Remove unsafe package from array and restart the validation loop to handle cascades
                if (( ! is_safe )); then
                    local -a new_safe=()
                    local keep_pkg
                    for keep_pkg in "${_safe_pkgs[@]}"; do
                        [[ "$keep_pkg" != "$pkg" ]] && new_safe+=("$keep_pkg")
                    done
                    _safe_pkgs=("${new_safe[@]}")
                    changed=1
                    break
                fi
            fi
        done
    done
    return 0
}

# ==============================================================================
# MAIN EXECUTION
# ==============================================================================

process_removals() {
    # Combine targets: Pacman handles repo and AUR packages identically for removal
    local -a ALL_TARGETS=("${REPO_TARGETS[@]}" "${AUR_TARGETS[@]}")

    if (( ${#ALL_TARGETS[@]} == 0 )); then
        log_warn "No packages configured for removal."
        return 0
    fi

    local -a active_targets=()
    filter_installed ALL_TARGETS active_targets

    if (( ${#active_targets[@]} == 0 )); then
        log_info "No targeted packages are currently installed."
        return 0
    fi

    local -a removable_targets=()
    resolve_safe_removals active_targets removable_targets

    if (( ${#removable_targets[@]} == 0 )); then
        log_info "No packages require removal (all active targets are dependencies of retained packages)."
        return 0
    fi

    local -a cmd=(sudo pacman -Rns)
    
    if (( AUTO_CONFIRM )); then
        cmd+=(--noconfirm)
    fi
    
    cmd+=(-- "${removable_targets[@]}")

    log_info "Removing ${BOLD}${#removable_targets[@]}${RESET} package(s):"
    printf '         %s%s%s\n' "${CYAN}" "${removable_targets[*]}" "${RESET}"

    if "${cmd[@]}"; then
        log_ok "Package removal completed successfully."
    else
        local -ri cmd_exit=$?
        log_err "Failed to remove some packages (exit code: ${cmd_exit})."
        EXIT_CODE=1
    fi
}

main() {
    parse_args "$@"
    check_environment

    if (( AUTO_CONFIRM )); then
        log_info "Mode: ${YELLOW}Autonomous (--noconfirm)${RESET}"
    fi

    printf '%s\n' "${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    process_removals
    printf '%s\n' "${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

    if (( EXIT_CODE == 0 )); then
        log_ok "Cleanup completed successfully."
    else
        log_warn "Cleanup completed with errors."
    fi

    return "$EXIT_CODE"
}

main "$@"
