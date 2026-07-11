#!/usr/bin/env bash
# Clipboard Persistnace Ram/disk
# -----------------------------------------------------------------------------
# Clipboard Persistence Manager - v1.3.0 (Static State Architecture)
# -----------------------------------------------------------------------------

set -euo pipefail

# =============================================================================
# ANSI Constants
# =============================================================================
declare -r C_RESET=$'\033[0m'
declare -r C_RED=$'\033[0;31m'
declare -r C_GREEN=$'\033[0;32m'
declare -r C_BLUE=$'\033[0;34m'
declare -r C_YELLOW=$'\033[1;33m'
declare -r C_BOLD=$'\033[1m'

# =============================================================================
# Configuration
# =============================================================================
declare -r STATE_DIR="${HOME}/.config/dusky/settings"
declare -r STATE_FILE="${STATE_DIR}/clipboard_persistance"
declare -r DB_ENV_FILE="${STATE_DIR}/cliphist_db_env"

# =============================================================================
# Argument Parsing
# =============================================================================
declare _TARGET_MODE=""
declare _QUIET_MODE="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ram)
            _TARGET_MODE="ephemeral"
            shift
            ;;
        --disk)
            _TARGET_MODE="persistent"
            shift
            ;;
        --quiet)
            _QUIET_MODE="true"
            shift
            ;;
        *)
            printf '%s[ERROR]%s Unknown argument: %s\n' "$C_RED" "$C_RESET" "$1" >&2
            exit 1
            ;;
    esac
done

# =============================================================================
# Logging
# =============================================================================
log_info()    { printf '%s[INFO]%s %s\n'    "$C_BLUE"   "$C_RESET" "$1"; }
log_success() { printf '%s[SUCCESS]%s %s\n' "$C_GREEN"  "$C_RESET" "$1"; }
log_warn()    { printf '%s[WARN]%s %s\n'    "$C_YELLOW" "$C_RESET" "$1"; }
log_err()     { printf '%s[ERROR]%s %s\n'   "$C_RED"    "$C_RESET" "$1" >&2; }

trap 'exit 130' INT
trap 'exit 143' TERM

# =============================================================================
# Pre-flight Checks
# =============================================================================
if (( BASH_VERSINFO[0] < 5 )); then
    log_err "Bash 5.0+ required."
    exit 1
fi

if [[ -z "$_TARGET_MODE" && ! -t 0 ]]; then
    log_err "Interactive TTY required."
    log_info "Use --ram or --disk for non-interactive mode."
    exit 1
fi

if [[ $EUID -eq 0 ]]; then
    log_err "Do NOT run this script as root/sudo."
    exit 1
fi

# =============================================================================
# Core Logic — Static File Write
# =============================================================================
update_config() {
    local mode="$1"
    mkdir -p "$STATE_DIR"

    if [[ "$mode" == "ephemeral" ]]; then
        printf 'export CLIPHIST_DB_PATH="%s/cliphist.db"\n' "${XDG_RUNTIME_DIR}" > "$DB_ENV_FILE"
        echo "false" > "$STATE_FILE"
        log_success "Set to Ephemeral (RAM). State file updated."
    elif [[ "$mode" == "persistent" ]]; then
        # EXPLICITLY set the disk path to violently override any global pollution
        local cache_dir="${XDG_CACHE_HOME:-$HOME/.cache}"
        mkdir -p "${cache_dir}/cliphist"
        printf 'export CLIPHIST_DB_PATH="%s/cliphist/db"\n' "${cache_dir}" > "$DB_ENV_FILE"
        echo "true" > "$STATE_FILE"
        log_success "Set to Persistent (Disk). State file updated."
    fi
    return 0
}

# =============================================================================
# User Interface
# =============================================================================
if [[ -n "$_TARGET_MODE" ]]; then
    if [[ "$_TARGET_MODE" == "ephemeral" ]]; then
        log_info "Applying Ephemeral settings (--ram)..."
        update_config "ephemeral"
    elif [[ "$_TARGET_MODE" == "persistent" ]]; then
        log_info "Applying Persistent settings (--disk)..."
        update_config "persistent"
    fi
else
    # Since standard output/error are redirected to a pipe by the orchestrator,
    # we redirect the interactive menu prompt and user input to/from /dev/tty
    # to bypass the pipe buffering and display the menu directly to the user.
    {
        clear
        printf '%sClipboard Persistence Manager%s\n' "$C_BOLD" "$C_RESET"
        printf 'Target: %s\n\n' "$DB_ENV_FILE"

        printf '%sWhich mode do you prefer?%s\n\n' "$C_BOLD" "$C_RESET"

        printf '  %s1) Ephemeral (RAM-based)%s\n' "$C_BOLD" "$C_RESET"
        printf '     - Clipboard history is stored in RAM.\n'
        printf '     - It %sdisappears%s when you reboot or shutdown.\n' "$C_RED" "$C_RESET"
        printf '     - Good for privacy and saving disk writes.\n\n'

        printf '  %s2) Persistent (Disk-based)%s\n' "$C_BOLD" "$C_RESET"
        printf '     - Clipboard history is stored on your hard drive.\n'
        printf '     - Your history %sstays available%s even after you reboot.\n' "$C_GREEN" "$C_RESET"
        printf '     - Standard behavior for most users.\n\n'

        read -rp "Select option [1/2] (default: 1): " choice
        choice="${choice:-1}"
    } > /dev/tty 2>&1 < /dev/tty

    case "$choice" in
        1) update_config "ephemeral" ;;
        2) update_config "persistent" ;;
        *) log_err "Invalid selection. Exiting."; exit 1 ;;
    esac
fi

# =============================================================================
# Post-Process (Live Daemon Reload)
# =============================================================================
printf '\n'
log_info "Live-reloading clipboard daemons in background..."

# 1. Source local shell environment & import to systemd/dbus to keep env consistent
if [[ -f "$DB_ENV_FILE" ]]; then
    source "$DB_ENV_FILE"
    export CLIPHIST_DB_PATH
    timeout 5 systemctl --user import-environment CLIPHIST_DB_PATH || true
    timeout 5 dbus-update-activation-environment --systemd CLIPHIST_DB_PATH || true
else
    unset CLIPHIST_DB_PATH
    timeout 5 systemctl --user unset-environment CLIPHIST_DB_PATH || true
    timeout 5 dbus-update-activation-environment --systemd --remove CLIPHIST_DB_PATH || true
fi

# 2. Terminate existing watchers securely
pkill -f "wl-paste.*cliphist" 2>/dev/null || :

# 3. Respawn the daemons directly in the background
sh -c '. $HOME/.config/dusky/settings/cliphist_db_env && exec wl-paste --type text --watch cliphist store' >/dev/null 2>&1 &
sh -c '. $HOME/.config/dusky/settings/cliphist_db_env && exec wl-paste --type image --watch cliphist store' >/dev/null 2>&1 &
disown -a

log_success "Daemons reloaded in background. New persistence mode is now active!"

