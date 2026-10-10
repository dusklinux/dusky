#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Name:        Arch/Hyprland Tailscale Teardown
# Description: Disables, Resets, or Uninstalls Tailscale.
#              (Companion to 01_tailscale_setup.sh)
# Version:     1.2.1
# -----------------------------------------------------------------------------

# --- Strict Mode & Safety ---
set -euo pipefail
shopt -s inherit_errexit

# --- Constants ---
declare -r LOCKFILE="/run/lock/dusky-tailscale.lock"
declare -r NM_CONF="/etc/NetworkManager/conf.d/96-tailscale.conf"

# --- Colors ---
if [[ -t 1 ]]; then
    declare -r R=$'\e[31m' G=$'\e[32m' Y=$'\e[33m' B=$'\e[34m' C=$'\e[36m' W=$'\e[0m'
else
    declare -r R="" G="" Y="" B="" C="" W=""
fi

# --- Helper Functions ---
log_info()  { printf "%s[INFO]%s  %s\n" "$B" "$W" "$*"; }
log_succ()  { printf "%s[OK]%s    %s\n" "$G" "$W" "$*"; }
log_warn()  { printf "%s[WARN]%s  %s\n" "$Y" "$W" "$*" >&2; }
log_error() { printf "%s[ERROR]%s %s\n" "$R" "$W" "$*" >&2; }
log_step()  { printf "\n%s[STEP]%s %s\n" "$C" "$W" "$*"; }

die() {
    log_error "$*"
    exit 1
}

cleanup() {
    local exit_code=$?
    if (( exit_code != 0 && exit_code != 130 && exit_code != 143 )); then
        printf "\n%s[FATAL]%s Script terminated with error code %d.\n" "$R" "$W" "$exit_code" >&2
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cmd_exists() { command -v "$1" &>/dev/null; }
pkg_installed() { pacman -Q "$1" &>/dev/null; }

remove_firewalld_interface() {
    local result
    if "$@" --zone=trusted --query-interface=tailscale0 >/dev/null; then
        "$@" --zone=trusted --remove-interface=tailscale0 >/dev/null
    else
        result=$?
        (( result == 1 )) || die "Could not inspect the Tailscale firewalld assignment."
    fi
}

# --- Pre-Flight Checks ---
if (( EUID != 0 )); then
    log_info "Escalating permissions..."
    script_path=$(realpath -- "${BASH_SOURCE[0]}")
    exec sudo --preserve-env=TERM bash "$script_path" "$@"
fi

[[ -f /etc/arch-release ]] || die "This script targets Arch Linux."

exec 9> "$LOCKFILE"
flock -n 9 || die "Another networking setup or teardown is running."

# --- Logic ---

log_step "Tailscale Teardown"
printf "This script can disable Tailscale temporarily, reset it, or remove it completely.\n\n"

printf "%sChoose an option:%s\n" "$C" "$W"
printf "  %s[1]%s Disable (Turn off VPN, keep login & install)\n" "$G" "$W"
printf "  %s[2]%s Reset Identity (Keep install, re-authenticate on next setup)\n" "$B" "$W"
printf "  %s[3]%s Full Uninstall (Remove package and standard local Tailscale state)\n" "$R" "$W"
printf "  %s[4]%s Cancel\n\n" "$Y" "$W"

printf "Select [1-4]: "
read -r choice || { log_info "Cancelled (end of input)."; exit 0; }

case "$choice" in
    1) MODE="DISABLE" ;;
    2) MODE="RESET" ;;
    3) MODE="UNINSTALL" ;;
    *) log_info "Cancelled."; exit 0 ;;
esac

# --- Action: Stop Service ---
log_step "Stopping Services"

unit_state=$(systemctl show tailscaled.service --property=LoadState --value)
if [[ "$unit_state" != "not-found" ]]; then
    active_state=$(systemctl show tailscaled.service --property=ActiveState --value)
    if [[ "$active_state" == active ]]; then
        if [[ "$MODE" == DISABLE ]]; then
            timeout --kill-after=2s 5s tailscale down --accept-risk=lose-ssh || log_warn "Could not disconnect via CLI; stopping the daemon."
        else
            # Logout already brings the connection down; no second down call is needed.
            timeout --kill-after=2s 5s tailscale logout || log_warn "Logout failed; local state will still be removed."
        fi
    fi
    systemctl disable --now tailscaled
    active_state=$(systemctl show tailscaled.service --property=ActiveState --value)
    case "$active_state" in
        inactive|failed) ;;
        *) die "Tailscaled did not stop ($active_state); refusing to delete its state." ;;
    esac
    log_succ "Service stopped and disabled."
else
    log_info "Tailscale service is not installed."
fi

if [[ "$MODE" == "DISABLE" ]]; then
    log_succ "Tailscale is disabled."
    exit 0
fi

# --- Action: Clean Data (Reset & Uninstall) ---
log_step "Cleaning State Data"

# Removing /var/lib/tailscale is what deletes the "Identity" (keys)
if [[ -d "/var/lib/tailscale" ]]; then
    log_info "Removing local state files (Identity/Keys)..."
    rm -rf /var/lib/tailscale
    log_succ "Identity wiped."
fi

if [[ -d "/var/cache/tailscale" ]]; then
    rm -rf /var/cache/tailscale
fi

# If resetting, we also want to clean configs so the setup script re-does them cleanly
log_info "Cleaning network configs..."

# Firewall
if cmd_exists firewall-cmd; then
    if systemctl is-active --quiet firewalld; then
        remove_firewalld_interface firewall-cmd
        remove_firewalld_interface firewall-cmd --permanent
    elif cmd_exists firewall-offline-cmd; then
        remove_firewalld_interface firewall-offline-cmd
    else
        die "Cannot remove permanent firewalld settings: firewall-offline-cmd is missing."
    fi
fi
if cmd_exists ufw; then
    ufw delete allow in on tailscale0 || die "Could not remove the Tailscale UFW rule."
fi

# NetworkManager
if [[ -f "$NM_CONF" ]]; then
    nm_config=$(< "$NM_CONF")
    case "$nm_config" in
        $'[keyfile]\nunmanaged-devices+=interface-name:tailscale0'|$'[keyfile]\nunmanaged-devices=interface-name:tailscale0')
            rm -f -- "$NM_CONF"
            if cmd_exists nmcli && systemctl is-active --quiet NetworkManager; then
                nmcli general reload conf
            fi
            ;;
        *) log_warn "Retaining edited NetworkManager configuration: $NM_CONF" ;;
    esac
fi
log_info "System resolver configuration and configuration backups are retained."

if [[ "$MODE" == "RESET" ]]; then
    printf "\n%s[SUCCESS]%s Tailscale identity has been reset.\n" "$G" "$W"
    printf "You can now run the Setup Script to re-authenticate. The assigned IP is determined by Tailscale.\n"
    exit 0
fi

# --- Action: Full Uninstall ---
log_step "Removing Software"

if pkg_installed tailscale; then
    log_info "Uninstalling Tailscale package..."
    pacman -Rns --noconfirm tailscale
    log_succ "Tailscale uninstalled."
else
    log_warn "Tailscale package not found (already removed?)."
fi

legacy_module_conf=/etc/modules-load.d/99-tailscale-uinput.conf
if [[ -f "$legacy_module_conf" ]]; then
    if [[ "$(< "$legacy_module_conf")" == uinput ]]; then
        rm -f -- "$legacy_module_conf"
    else
        log_warn "Retaining edited module configuration: $legacy_module_conf"
    fi
fi

printf "\n%s[SUCCESS]%s Tailscale package and standard local state have been removed.\n" "$G" "$W"
