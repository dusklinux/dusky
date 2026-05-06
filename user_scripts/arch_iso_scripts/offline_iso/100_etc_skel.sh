#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Script: configure_skel.sh
# Description: Stages dotfiles into /etc/skel with smart permissions.
# Context: Arch Linux ISO (Chroot Environment)
# -----------------------------------------------------------------------------

# =============================================================================
# STRICT MODE & SETTINGS
# =============================================================================
set -euo pipefail
shopt -s inherit_errexit nullglob 2>/dev/null || true

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

# =============================================================================
# VISUALS & LOGGING
# =============================================================================
if [[ -t 1 ]]; then
    declare -r BLUE=$'\033[0;34m'
    declare -r GREEN=$'\033[0;32m'
    declare -r RED=$'\033[0;31m'
    declare -r YELLOW=$'\033[0;33m'
    declare -r BOLD=$'\033[1m'
    declare -r NC=$'\033[0m'
else
    declare -r BLUE="" GREEN="" RED="" YELLOW="" BOLD="" NC=""
fi

log_info()    { printf "%s[INFO]%s %s\n" "$BLUE" "$NC" "$*"; }
log_warn()    { printf "%s[WARN]%s %s\n" "$YELLOW" "$NC" "$*" >&2; }
log_success() { printf "%s[SUCCESS]%s %s\n" "$GREEN" "$NC" "$*"; }
log_error()   { printf "%s[ERROR]%s %s\n" "$RED" "$NC" "$*" >&2; }
die()         { log_error "$*"; exit 1; }

# =============================================================================
# RUNTIME STATE
# =============================================================================
declare -i AUTO_MODE=0
declare -i WARNINGS=0
declare -i DEPLOYED_COUNT=0

# =============================================================================
# CONFIGURATION
# =============================================================================
# Format: "SOURCE :: DESTINATION"
# Note: Destinations must be explicit filenames or full directory paths.
#       The script uses 'cp -T' so it will NOT auto-nest directories.

declare -a COPY_TASKS=(
    # 0. User Scripts Directory (Directory contents)
    # "dusky/user_scripts/ :: /etc/skel/Documents/user_scripts"

    # 1. Deployment Script (Script -> Executable)
    "deploy_dotfiles.sh :: /etc/skel/deploy_dotfiles.sh"

    # 2. Zsh Config (Config -> Not Executable)
    "dusky/.zshrc :: /etc/skel/.zshrc"

    # 3. Network Manager Script
    "dusky/user_scripts/network_manager/dusky_network.sh :: /etc/skel/wifi_connect.sh"
)

# Files matching these patterns will be forced to be executable (755)
declare -a EXEC_PATTERNS=("*.sh" "*.bash" "*.pl" "*.py" "deploy_*")

# =============================================================================
# ARGUMENT PARSING
# =============================================================================
usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  -a, --auto    Run non-interactively; skip prompts
  -h, --help    Show this help message
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -a|--auto)
                AUTO_MODE=1
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                die "Unknown argument: $1 (use --help for usage)"
                ;;
        esac
        shift
    done
}

# =============================================================================
# PRE-FLIGHT CHECKS
# =============================================================================
check_root() {
    if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
        die "This script must be run as root to modify /etc/skel."
    fi
}

prompt_for_auto_mode() {
    if (( AUTO_MODE )); then
        log_info "Auto mode enabled. This script will run without prompts."
        return
    fi

    if [[ ! -t 0 ]]; then
        die "No interactive input available. Re-run with --auto to skip prompts."
    fi

    local reply
    if ! read -r -p "Run this script in auto mode and skip prompts? [y/N]: " reply; then
        die "Failed to read input."
    fi

    case "${reply,,}" in
        y|yes)
            AUTO_MODE=1
            log_info "Auto mode selected. Remaining prompts will be skipped."
            ;;
        *)
            ;;
    esac
}

preflight_confirmation() {
    if (( AUTO_MODE )); then
        log_info "Auto mode: skipping interactive chroot confirmation. Ensure you are already inside arch-chroot."
        return
    fi

    printf "\n%s[CRITICAL CHECK]%s Verify Environment:\n" "$RED" "$NC"
    printf "Have you switched to the chroot environment by running: %sarch-chroot /mnt%s ?\n" "$BLUE" "$NC"

    local user_conf
    if ! read -r -p "Type 'yes' to proceed, or anything else to exit: " user_conf; then
        die "Failed to read input."
    fi

    if [[ "${user_conf,,}" != "yes" ]]; then
        printf "\n%s[ABORTING]%s You must be inside the chroot environment.\n" "$RED" "$NC"
        printf "Please run:\n    %sarch-chroot /mnt%s\n\n" "$BLUE" "$NC"
        exit 1
    fi
}

# =============================================================================
# CORE FUNCTIONS
# =============================================================================
trim() {
    local s="$1"
    s="${s#"${s%%[![:space:]]*}"}"
    s="${s%"${s##*[![:space:]]}"}"
    printf '%s' "$s"
}

increment_warning() {
    WARNINGS=$((WARNINGS + 1))
}

is_safe_destination() {
    case "$1" in
        /etc/skel|/etc/skel/*) return 0 ;;
        *) return 1 ;;
    esac
}

resolve_source_path() {
    local source_path="$1"

    if [[ -e "$source_path" ]]; then
        printf '%s' "$source_path"
        return 0
    fi

    if [[ "$source_path" != /* && -e "$SCRIPT_DIR/$source_path" ]]; then
        printf '%s' "$SCRIPT_DIR/$source_path"
        return 0
    fi

    return 1
}

smart_permissions() {
    local target="$1"

    chown -R root:root -- "$target"

    if [[ -d "$target" ]]; then
        find "$target" -type d -exec chmod 755 {} +
        find "$target" -type f -exec chmod 644 {} +

        for pat in "${EXEC_PATTERNS[@]}"; do
            find "$target" -type f -name "$pat" -exec chmod 755 {} + 2>/dev/null || true
        done
    else
        local basename="${target##*/}"
        local is_exec=0

        for pat in "${EXEC_PATTERNS[@]}"; do
            # shellcheck disable=SC2053
            if [[ "$basename" == $pat ]]; then
                is_exec=1
                break
            fi
        done

        if [[ $is_exec -eq 1 ]]; then
            chmod 755 -- "$target"
        else
            chmod 644 -- "$target"
        fi
    fi
}

deploy_item() {
    local source_path="$1"
    local dest_path="$2"
    local resolved_source
    local dest_parent

    if ! is_safe_destination "$dest_path"; then
        log_warn "Destination '$dest_path' is not inside /etc/skel. Skipping for safety."
        increment_warning
        return
    fi

    if ! resolved_source="$(resolve_source_path "$source_path")"; then
        log_warn "Source not found: $source_path"
        increment_warning
        return
    fi

    dest_parent="$(dirname -- "$dest_path")"
    if [[ ! -d "$dest_parent" ]]; then
        mkdir -p -- "$dest_parent"
    fi

    log_info "Copying: $resolved_source -> $dest_path"
    cp -rfPT -- "$resolved_source" "$dest_path"

    smart_permissions "$dest_path"
    DEPLOYED_COUNT=$((DEPLOYED_COUNT + 1))
}

# =============================================================================
# MAIN EXECUTION
# =============================================================================
main() {
    parse_args "$@"
    check_root
    prompt_for_auto_mode
    preflight_confirmation

    if [[ ! -d "/etc/skel" ]]; then
        mkdir -p -- /etc/skel
    fi

    log_info "Starting Skeleton Configuration..."

    for task in "${COPY_TASKS[@]}"; do
        if [[ "$task" != *" :: "* ]]; then
            log_warn "Skipping malformed COPY_TASKS entry: $task"
            increment_warning
            continue
        fi

        local src="${task%% :: *}"
        local dest="${task##* :: }"

        src="$(trim "$src")"
        dest="$(trim "$dest")"

        if [[ -n "$src" && -n "$dest" ]]; then
            deploy_item "$src" "$dest"
        else
            log_warn "Skipping empty COPY_TASKS entry: $task"
            increment_warning
        fi
    done

    if (( WARNINGS > 0 )); then
        log_warn "Skeleton configuration completed with $WARNINGS warning(s). Deployed $DEPLOYED_COUNT item(s)."
    else
        log_success "Skeleton configuration complete. Deployed $DEPLOYED_COUNT item(s)."
    fi
}

main "$@"
