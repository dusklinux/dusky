#!/usr/bin/env bash
#d: Remove installed targets and unused dependencies without creating .pacsave files

set -euo pipefail
IFS=$' \t\n'

# This optional setup step must never stop the installer with a nonzero status.
# Keep diagnostics visible even when an unexpected error ends execution.
trap 'exit 0' EXIT PIPE
trap 'printf "\n[WARN] Package removal interrupted.\n" >&2; exit 0' HUP INT TERM

# Pacman manages both repository and AUR packages in its local database.
readonly -a REPO_TARGETS=(
    dunst
    dolphin
    wofi
    polkit-kde-agent
    power-profiles-daemon
    network-manager-applet
    firewalld
    hyprpolkitagent
)
readonly -a AUR_TARGETS=()

readonly SCRIPT_NAME=${0##*/}
readonly SCRIPT_VERSION=4.0.1

log_info() { printf '[INFO] %s\n' "$1"; }
log_warn() { printf '[WARN] %s\n' "$1" >&2; }

show_help() {
    printf '%s v%s — Arch package removal\n\n' "$SCRIPT_NAME" "$SCRIPT_VERSION"
    printf 'Usage: %s [OPTIONS]\n\n' "$SCRIPT_NAME"
    printf '%s\n' \
        '  -y, --auto     Skip pacman confirmation (default)' \
        '  -h, --help     Show this help' \
        '  -V, --version  Show version' \
        '' \
        'Required targets are skipped; unused dependencies are also removed.' \
        'No .pacsave files are created. Normal exits and handled signals return 0.'
}

main() {
    while (( $# )); do
        case $1 in
            -y|--auto) shift ;;
            -h|--help) show_help; return 0 ;;
            -V|--version)
                printf '%s v%s\n' "$SCRIPT_NAME" "$SCRIPT_VERSION"
                return 0
                ;;
            --) shift; break ;;
            *) log_warn "Unexpected argument: $1"; return 0 ;;
        esac
    done
    if (( $# )); then
        log_warn "Unexpected argument: $1"
        return 0
    fi

    local -a targets=("${REPO_TARGETS[@]}" "${AUR_TARGETS[@]}")
    if (( ${#targets[@]} == 0 )); then
        log_info 'No packages configured for removal.'
        return 0
    fi

    if ! command -v pacman >/dev/null 2>&1; then
        log_warn 'Pacman is unavailable; skipping package removal.'
        return 0
    fi

    # Capture query status directly: process substitution hides query failures.
    # A single database query also avoids treating package groups as targets.
    local installed_names pkg
    if ! installed_names=$(pacman -Qq); then
        log_warn 'Could not query installed packages; skipping package removal.'
        return 0
    fi

    local -A installed=()
    while IFS= read -r pkg; do
        if [[ -n $pkg ]]; then
            installed["$pkg"]=1
        fi
    done <<< "$installed_names"

    local -a active_targets=()
    for pkg in "${targets[@]}"; do
        [[ -n $pkg ]] || continue
        if [[ -v installed[$pkg] ]]; then
            active_targets+=("$pkg")
            unset 'installed[$pkg]' # Remove duplicate configured targets.
        else
            log_info "Skipping '$pkg': absent or already selected."
        fi
    done

    if (( ${#active_targets[@]} == 0 )); then
        log_info 'No targeted packages are installed.'
        return 0
    fi

    local -a cmd=(pacman)
    if (( EUID != 0 )); then
        if ! command -v sudo >/dev/null 2>&1; then
            log_warn 'Sudo is unavailable; skipping package removal.'
            return 0
        fi
        cmd=(sudo pacman)
    fi

    # Resolve dependencies inside pacman's transaction, including mutual
    # dependencies and virtual providers. Retain targets needed by other packages.
    cmd+=(-Rns --unneeded --noconfirm -- "${active_targets[@]}")
    log_info "Requesting removal of: ${active_targets[*]}"
    if "${cmd[@]}"; then
        log_info 'Package removal finished; required targets were retained.'
    else
        local status=$?
        log_warn "Package removal did not complete (status $status); continuing setup."
    fi
    return 0
}

main "$@"
