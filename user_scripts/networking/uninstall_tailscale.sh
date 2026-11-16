#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Name:        Arch/Hyprland Tailscale Teardown
# Description: Disables, Resets, or Uninstalls Tailscale safely and idempotently.
#              (Companion to 01_tailscale_setup.sh)
# Version:     2.1.0
# Target OS:   Arch Linux (systemd 262+, GNU Bash 5.3+, Linux 7.3+)
# Policy:      Zero Fatal Exit Policy — guaranteed exit code 0 for orchestrators.
# -----------------------------------------------------------------------------

# --- Strict Mode & Safety ---
set -euo pipefail
shopt -s inherit_errexit

# --- Environment Hygiene ---
export PATH="/usr/local/sbin:/usr/local/bin:/usr/bin:${PATH:-}"
IFS=$' \t\n'

# --- Constants ---
declare -r LOCKDIR="/run/lock"
declare -r LOCKFILE="${LOCKDIR}/dusky-tailscale.lock"
declare -r NM_CONF="/etc/NetworkManager/conf.d/96-tailscale.conf"
declare -r LEGACY_MODULE_CONF="/etc/modules-load.d/99-tailscale-uinput.conf"
declare -r STATE_DIR="/var/lib/tailscale"
declare -r CACHE_DIR="/var/cache/tailscale"
declare -r PACMAN_LOCK="/var/lib/pacman/db.lck"

# --- Runtime State ---
declare -i WARN_COUNT=0
declare -i LOCK_ACQUIRED=0

# --- Colors ---
if [[ -t 1 ]]; then
    declare -r R=$'\e[31m' G=$'\e[32m' Y=$'\e[33m' B=$'\e[34m' C=$'\e[36m' W=$'\e[0m'
else
    declare -r R="" G="" Y="" B="" C="" W=""
fi

# --- Logging & Non-Fatal Error Handling ---
# All printf calls are guarded with '|| true' alongside 'trap "" PIPE' so a closed
# downstream stdout/stderr pipe can never trigger SIGPIPE (141) or set -e failure.
log_info()  { printf "%s[INFO]%s  %s\n" "$B" "$W" "$*" || true; }
log_succ()  { printf "%s[OK]%s    %s\n" "$G" "$W" "$*" || true; }
log_warn()  {
    WARN_COUNT=$(( WARN_COUNT + 1 ))
    printf "%s[WARN]%s  %s\n" "$Y" "$W" "$*" >&2 || true
}
# shellcheck disable=SC2329
log_error() {
    WARN_COUNT=$(( WARN_COUNT + 1 ))
    printf "%s[ERROR]%s %s\n" "$R" "$W" "$*" >&2 || true
}
log_step()  { printf "\n%s[STEP]%s %s\n" "$C" "$W" "$*" || true; }

# Soft exit helper: logs a warning and exits 0 so orchestrators never abort.
finish_safely() {
    if (( $# > 0 )); then
        log_warn "$*"
    fi
    exit 0
}

# Ultimate safety net: release shared lock description and guarantee exit 0.
# shellcheck disable=SC2329
cleanup() {
    local exit_code=$?
    trap - EXIT INT TERM

    if (( LOCK_ACQUIRED == 1 )); then
        flock -u 9 2>/dev/null || true
        exec 9>&- 2>/dev/null || true
    fi

    if (( exit_code != 0 )); then
        printf "\n%s[WARN]%s Teardown caught unexpected status (%d); suppressing fatal exit for orchestrator safety.\n" \
            "$Y" "$W" "$exit_code" >&2 || true
    fi
    exit 0
}
trap '' PIPE
trap cleanup EXIT
trap 'log_warn "Interrupted by SIGINT; exiting cleanly."; exit 0' INT
trap 'log_warn "Terminated by SIGTERM; exiting cleanly."; exit 0' TERM

# --- Utility Functions ---
cmd_exists() {
    command -v "$1" &>/dev/null
}

pkg_installed() {
    pacman -Q "$1" &>/dev/null
}

show_usage() {
    cat <<EOF || true
Usage: ${0##*/} [OPTION]

Options:
  -u, --uninstall, --full   Stop daemon, wipe state/configs, and uninstall Tailscale package
  -r, --reset               Stop daemon and wipe local identity/configs (keep package installed)
  -d, --disable             Disconnect VPN and disable tailscaled service (keep identity & package)
  -h, --help                Display this help message and exit 0

If no option is provided:
  - Interactive TTY:        Presents an interactive selection menu
  - Non-interactive stdin:  Reads piped input if provided, or exits 0 cleanly on EOF/timeout
EOF
}

get_unit_property() {
    local unit="$1"
    local prop="$2"
    local val=""
    val=$(timeout --kill-after=2s 5s systemctl show "$unit" --property="$prop" --value 2>/dev/null || true)
    printf '%s' "$val"
}

remove_firewalld_interface() {
    local cmd=("$@")
    local rc=0

    timeout --kill-after=2s 10s "${cmd[@]}" --zone=trusted --query-interface=tailscale0 &>/dev/null || rc=$?
    if (( rc == 0 )); then
        if timeout --kill-after=2s 10s "${cmd[@]}" --zone=trusted --remove-interface=tailscale0 &>/dev/null; then
            log_info "Removed tailscale0 from firewalld trusted zone (${cmd[*]})."
        else
            log_warn "Failed to remove tailscale0 from firewalld trusted zone (${cmd[*]})."
        fi
    elif (( rc != 1 )); then
        log_warn "Could not inspect firewalld trusted zone via '${cmd[*]}' (exit code $rc); skipping."
    fi
    return 0
}

wait_for_pacman_lock() {
    local -i max_wait=30
    local -i elapsed=0

    while [[ -e "$PACMAN_LOCK" ]]; do
        if (( elapsed == 0 )); then
            log_info "Pacman database lock ($PACMAN_LOCK) detected; waiting up to ${max_wait}s..."
        fi
        if (( elapsed >= max_wait )); then
            if ! pgrep -x pacman &>/dev/null; then
                log_warn "Pacman lock ($PACMAN_LOCK) persists after ${max_wait}s with no running pacman process (potentially stale lock); skipping package removal."
            else
                log_warn "Pacman database is still locked by an active process after ${max_wait}s; skipping package removal."
            fi
            return 1
        fi
        sleep 2
        elapsed=$(( elapsed + 2 ))
    done
    return 0
}

stop_and_disable_tailscaled() {
    local mode="$1"
    local load_state active_state unit_file_state

    log_step "Stopping Services"

    if ! cmd_exists systemctl; then
        log_warn "systemctl not found; skipping systemd service management."
        return 0
    fi

    load_state=$(get_unit_property "tailscaled.service" "LoadState")
    active_state=$(get_unit_property "tailscaled.service" "ActiveState")
    unit_file_state=$(get_unit_property "tailscaled.service" "UnitFileState")

    # Handle persistent and runtime masked units before attempting operations
    if [[ "$load_state" == "masked" || "$unit_file_state" == "masked" || "$unit_file_state" == "masked-runtime" ]]; then
        log_info "tailscaled.service is currently masked."
        if [[ "$mode" != "DISABLE" ]]; then
            timeout --kill-after=2s 10s systemctl unmask tailscaled.service &>/dev/null || true
            timeout --kill-after=2s 10s systemctl unmask --runtime tailscaled.service &>/dev/null || true
            load_state=$(get_unit_property "tailscaled.service" "LoadState")
            if [[ "$load_state" == "masked" ]]; then
                log_warn "Could not unmask tailscaled.service."
            fi
        fi
    fi

    if [[ -z "$load_state" || "$load_state" == "not-found" ]] && ! pgrep -x tailscaled &>/dev/null; then
        log_info "Tailscale service is not installed or running."
        return 0
    fi

    # 1. Graceful CLI disconnect / logout if daemon is responsive
    if [[ "$active_state" == "active" ]] && cmd_exists tailscale; then
        if [[ "$mode" == "DISABLE" ]]; then
            log_info "Disconnecting Tailscale VPN..."
            timeout --kill-after=2s 5s tailscale down --accept-risk=lose-ssh &>/dev/null \
                || log_warn "Could not disconnect via Tailscale CLI; proceeding to stop daemon."
        else
            log_info "Logging out of Tailscale node..."
            timeout --kill-after=2s 5s tailscale logout &>/dev/null \
                || log_warn "Tailscale logout timed out or failed (network down or already logged out); local state will still be wiped."
        fi
    fi

    # 2. Stop the service via systemd with timeout
    if [[ "$active_state" != "inactive" && -n "$active_state" ]]; then
        log_info "Stopping tailscaled.service..."
        timeout --kill-after=3s 15s systemctl stop tailscaled.service &>/dev/null \
            || log_warn "systemctl stop tailscaled.service did not complete cleanly."
    fi

    # 3. Verify stopped state; escalate to SIGKILL if still running
    active_state=$(get_unit_property "tailscaled.service" "ActiveState")
    if [[ "$active_state" == "active" || "$active_state" == "activating" || "$active_state" == "deactivating" || "$active_state" == "reloading" ]] \
        || pgrep -x tailscaled &>/dev/null; then
        log_warn "tailscaled did not stop cleanly (state: ${active_state:-unknown}); escalating to SIGKILL..."
        timeout --kill-after=2s 5s systemctl kill --signal=SIGKILL tailscaled.service &>/dev/null || true
        pkill -9 -x tailscaled &>/dev/null || true

        local -i retries=15
        while (( retries > 0 )); do
            active_state=$(get_unit_property "tailscaled.service" "ActiveState")
            if [[ "$active_state" == "inactive" || "$active_state" == "failed" || -z "$active_state" ]] \
                && ! pgrep -x tailscaled &>/dev/null; then
                break
            fi
            sleep 0.2
            retries=$(( retries - 1 ))
        done
    fi

    # 4. Disable unit if loaded
    load_state=$(get_unit_property "tailscaled.service" "LoadState")
    if [[ "$load_state" == "loaded" ]]; then
        timeout --kill-after=2s 10s systemctl disable tailscaled.service &>/dev/null \
            || log_warn "Could not disable tailscaled.service (unit file may already be removed)."
    fi

    # Clear any failed state from SIGKILL
    timeout --kill-after=2s 5s systemctl reset-failed tailscaled.service &>/dev/null || true

    # 5. Clean up residual kernel TUN interface if lingering
    if [[ "$mode" != "DISABLE" ]] && cmd_exists ip; then
        if ip link show dev tailscale0 &>/dev/null; then
            ip link delete dev tailscale0 &>/dev/null \
                || log_warn "Could not remove residual tailscale0 interface."
        fi
    fi

    log_succ "Service stopped and disabled."
    return 0
}

clean_state_and_network() {
    log_step "Cleaning State Data & Network Configuration"

    # 1. Wipe Identity / Keys (/var/lib/tailscale)
    if [[ -e "$STATE_DIR" || -L "$STATE_DIR" ]]; then
        log_info "Removing local state files ($STATE_DIR)..."
        if rm -rf -- "$STATE_DIR" 2>/dev/null; then
            log_succ "Identity and state wiped."
        else
            log_warn "Failed to completely remove $STATE_DIR."
        fi
    else
        log_info "State directory $STATE_DIR already absent."
    fi

    # 2. Wipe Cache (/var/cache/tailscale)
    if [[ -e "$CACHE_DIR" || -L "$CACHE_DIR" ]]; then
        log_info "Removing cache directory ($CACHE_DIR)..."
        rm -rf -- "$CACHE_DIR" 2>/dev/null || log_warn "Failed to completely remove $CACHE_DIR."
    fi

    log_info "Cleaning firewall and network configurations..."

    # 3. Firewalld Cleanup (Runtime & Permanent)
    if cmd_exists firewall-cmd && timeout --kill-after=2s 5s systemctl is-active --quiet firewalld 2>/dev/null; then
        remove_firewalld_interface firewall-cmd
        remove_firewalld_interface firewall-cmd --permanent
    elif cmd_exists firewall-offline-cmd; then
        remove_firewalld_interface firewall-offline-cmd
    fi

    # 4. UFW Cleanup
    if cmd_exists ufw; then
        local ufw_status=""
        ufw_status=$(LC_ALL=C timeout --kill-after=2s 5s ufw status 2>/dev/null || true)
        if [[ "$ufw_status" == *"Status: active"* && "$ufw_status" == *"tailscale0"* ]]; then
            if timeout --kill-after=2s 10s ufw --force delete allow in on tailscale0 &>/dev/null; then
                log_info "Removed UFW rule for tailscale0."
            else
                log_warn "UFW rule removal for tailscale0 returned non-zero."
            fi
        fi
    fi

    # 5. NetworkManager Unmanaged Device Config Cleanup
    if [[ -f "$NM_CONF" || -L "$NM_CONF" ]]; then
        local nm_config=""
        nm_config=$(cat -- "$NM_CONF" 2>/dev/null || true)
        nm_config="${nm_config//$'\r'/}"

        case "$nm_config" in
            $'[keyfile]\nunmanaged-devices+=interface-name:tailscale0'|\
            $'[keyfile]\nunmanaged-devices=interface-name:tailscale0'|\
            "")
                if rm -f -- "$NM_CONF" 2>/dev/null; then
                    log_info "Removed NetworkManager configuration: $NM_CONF"
                    if cmd_exists nmcli && timeout --kill-after=2s 5s systemctl is-active --quiet NetworkManager 2>/dev/null; then
                        timeout --kill-after=2s 5s nmcli general reload conf &>/dev/null \
                            || log_warn "NetworkManager config reload failed; changes will apply on next NM reload."
                    fi
                else
                    log_warn "Could not remove $NM_CONF."
                fi
                ;;
            *)
                log_warn "Retaining user-modified NetworkManager configuration: $NM_CONF"
                ;;
        esac
    fi

    log_info "System resolver configuration and configuration backups are retained."
    return 0
}

remove_package_and_modules() {
    log_step "Removing Software & Module Configs"

    # 1. Remove Tailscale package via pacman (stdin isolated from pipe via </dev/null)
    if pkg_installed tailscale; then
        if wait_for_pacman_lock; then
            log_info "Uninstalling tailscale package..."
            if pacman -Rns --noconfirm tailscale </dev/null; then
                log_succ "Tailscale package uninstalled."
            else
                log_warn "pacman -Rns failed (possible reverse dependency); attempting fallback 'pacman -R --noconfirm tailscale'..."
                if wait_for_pacman_lock && pacman -R --noconfirm tailscale </dev/null; then
                    log_succ "Tailscale package uninstalled via fallback."
                else
                    log_warn "Failed to uninstall tailscale package via pacman; manual check recommended."
                fi
            fi
        fi
    else
        log_info "Tailscale package is not installed (already removed)."
    fi

    # 2. Clean legacy uinput module drop-in
    if [[ -f "$LEGACY_MODULE_CONF" || -L "$LEGACY_MODULE_CONF" ]]; then
        local mod_content=""
        mod_content=$(cat -- "$LEGACY_MODULE_CONF" 2>/dev/null || true)
        mod_content="${mod_content//$'\r'/}"
        if [[ "$mod_content" == "uinput" || -z "$mod_content" ]]; then
            if rm -f -- "$LEGACY_MODULE_CONF" 2>/dev/null; then
                log_info "Removed module configuration: $LEGACY_MODULE_CONF"
            else
                log_warn "Failed to remove $LEGACY_MODULE_CONF."
            fi
        else
            log_warn "Retaining user-modified module configuration: $LEGACY_MODULE_CONF"
        fi
    fi

    # 3. Reload systemd daemon if available
    if cmd_exists systemctl; then
        timeout --kill-after=2s 10s systemctl daemon-reload &>/dev/null || true
    fi

    return 0
}

# --- Pre-Flight: Help Flag (Immediate, No Root Escalation) ---
for arg in "$@"; do
    case "$arg" in
        --help|-h)
            show_usage
            exit 0
            ;;
    esac
done

# --- Privilege Escalation (Preserving Arguments & Parent EXIT Trap) ---
if (( EUID != 0 )); then
    log_info "Escalating permissions via sudo..."
    script_path=$(realpath -- "${BASH_SOURCE[0]}" 2>/dev/null || printf '%s' "${BASH_SOURCE[0]}")
    if [[ -t 0 ]]; then
        if sudo --preserve-env=TERM bash -- "$script_path" "$@"; then
            exit 0
        else
            finish_safely "Privilege escalation failed or was cancelled by user."
        fi
    else
        if sudo -n true 2>/dev/null; then
            if sudo -n --preserve-env=TERM bash -- "$script_path" "$@"; then
                exit 0
            else
                finish_safely "Non-interactive sudo escalation failed."
            fi
        else
            finish_safely "Root privileges required, but session is non-interactive and sudo credentials are not cached."
        fi
    fi
fi

# --- CLI Argument Parsing (Executed as Root) ---
MODE=""
while (( $# > 0 )); do
    case "$1" in
        --uninstall|--full|-u)
            MODE="UNINSTALL"
            shift
            ;;
        --disable|-d)
            MODE="DISABLE"
            shift
            ;;
        --reset|-r)
            MODE="RESET"
            shift
            ;;
        --help|-h)
            show_usage
            exit 0
            ;;
        "")
            shift
            ;;
        *)
            log_warn "Ignoring unrecognized argument: '$1'"
            shift
            ;;
    esac
done

# --- OS Verification ---
if [[ ! -f /etc/arch-release ]]; then
    finish_safely "This script targets Arch Linux (/etc/arch-release not found). Skipping teardown."
fi

# --- Concurrency Lock ---
mkdir -p -- "$LOCKDIR" 2>/dev/null || true
if exec 9> "$LOCKFILE" 2>/dev/null; then
    if flock -w 15 9; then
        LOCK_ACQUIRED=1
    else
        exec 9>&- 2>/dev/null || true
        finish_safely "Another Tailscale setup or teardown instance holds $LOCKFILE. Exiting cleanly."
    fi
else
    log_warn "Could not open lockfile $LOCKFILE; continuing without file lock."
fi

# --- Interactive Menu / Piped Input Handling ---
if [[ -z "$MODE" ]]; then
    choice=""
    if [[ -t 0 ]]; then
        log_step "Tailscale Teardown"
        printf "This script can disable Tailscale temporarily, reset it, or remove it completely.\n\n" || true

        printf "%sChoose an option:%s\n" "$C" "$W" || true
        printf "  %s[1]%s Disable (Turn off VPN, keep login & install)\n" "$G" "$W" || true
        printf "  %s[2]%s Reset Identity (Keep install, re-authenticate on next setup)\n" "$B" "$W" || true
        printf "  %s[3]%s Full Uninstall (Remove package and standard local Tailscale state)\n" "$R" "$W" || true
        printf "  %s[4]%s Cancel\n\n" "$Y" "$W" || true

        printf "Select [1-4]: " || true
        if ! read -r choice; then
            log_info "No input detected; exiting cleanly."
            exit 0
        fi
    else
        # Non-TTY stdin: support piped input (e.g. printf '3\n' | ...), exit 0 on /dev/null EOF,
        # and bound wait to 2s so an unclosed orchestrator pipe can never hang indefinitely.
        if ! read -r -t 2 choice; then
            log_info "No input detected on non-interactive stdin; exiting cleanly."
            exit 0
        fi
    fi

    case "$choice" in
        1) MODE="DISABLE" ;;
        2) MODE="RESET" ;;
        3) MODE="UNINSTALL" ;;
        *)
            log_info "Cancelled."
            exit 0
            ;;
    esac
fi

# --- Execute Selected Mode ---
stop_and_disable_tailscaled "$MODE"

if [[ "$MODE" == "DISABLE" ]]; then
    if (( WARN_COUNT > 0 )); then
        printf "\n%s[SUCCESS]%s Tailscale is disabled (%d warning(s) logged).\n" "$G" "$W" "$WARN_COUNT" || true
    else
        printf "\n%s[SUCCESS]%s Tailscale is disabled.\n" "$G" "$W" || true
    fi
    exit 0
fi

clean_state_and_network

if [[ "$MODE" == "RESET" ]]; then
    if (( WARN_COUNT > 0 )); then
        printf "\n%s[SUCCESS]%s Tailscale identity has been reset (%d warning(s) logged).\n" "$G" "$W" "$WARN_COUNT" || true
    else
        printf "\n%s[SUCCESS]%s Tailscale identity has been reset.\n" "$G" "$W" || true
    fi
    printf "You can now run the Setup Script to re-authenticate. The assigned IP is determined by Tailscale.\n" || true
    exit 0
fi

remove_package_and_modules

if (( WARN_COUNT > 0 )); then
    printf "\n%s[SUCCESS]%s Tailscale teardown completed with %d non-fatal warning(s).\n" "$G" "$W" "$WARN_COUNT" || true
else
    printf "\n%s[SUCCESS]%s Tailscale package and standard local state have been removed.\n" "$G" "$W" || true
fi
exit 0
