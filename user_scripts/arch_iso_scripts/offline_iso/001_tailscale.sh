#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Name:        Arch ISO Remote Network Setup (Tailscale Only)
# Description: Automated setup for CGNAT-friendly remote access networking.
#              Strictly optimized for the ephemeral Arch Linux Live ISO.
# Version:     3.2.0 (ISO Branch)
# -----------------------------------------------------------------------------

# --- Strict Mode & Modernity ---
set -euo pipefail
shopt -s inherit_errexit

# --- Constants ---
declare -r SCRIPT_NAME="${0##*/}"
declare -r LOCKFILE="/var/lock/${SCRIPT_NAME}.lock"
declare -r RESOLV_CONF="/etc/resolv.conf"
declare -r STUB_RESOLV="/run/systemd/resolve/stub-resolv.conf"

export TERM="${TERM:-xterm-256color}"

# --- Color Definitions ---
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

pkg_installed() { pacman -Q "$1" &>/dev/null; }
svc_active() { systemctl is-active --quiet "$1" 2>/dev/null; }

# --- Pre-Flight Checks ---
(( BASH_VERSINFO[0] >= 5 )) || die "Bash 5.0+ required. Current: $BASH_VERSION"
(( EUID == 0 )) || die "Must run as root. This is the Arch ISO environment."
[[ -f /etc/arch-release ]] || die "This script is strictly optimized for Arch Linux."

# Atomic Lock
exec 9> "$LOCKFILE"
flock -n 9 || die "Another instance is running."

# --- Main Logic ---
log_step "Initializing Arch ISO Setup..."
printf "Configuring Tailscale for Live Environment Networking.\n"
printf "%sTarget Environment:%s Arch Linux Live ISO\n\n" "$C" "$W"

# --- Phase 0: System Foundation ---
log_step "Phase 0: System Foundation"

log_info "Configuring systemd-resolved (Arch ISO Default)..."
svc_active systemd-resolved || systemctl enable --now systemd-resolved

timeout=10
while [[ ! -f "$STUB_RESOLV" ]] && (( timeout > 0 )); do
    sleep 1; ((timeout--))
done

if [[ -f "$STUB_RESOLV" ]]; then
    ln -sf "$STUB_RESOLV" "$RESOLV_CONF"
    log_succ "DNS linked to systemd-resolved stub."
fi

# Ensure TUN module is loaded for Tailscale
modprobe tun || log_warn "Failed to modprobe tun. Tailscale may fail."

# --- Phase 1: Tailscale ---
log_step "Phase 1: Tailscale Network"

if ! pkg_installed tailscale; then
    log_info "Synchronizing ephemeral pacman DB and installing Tailscale..."
    # CRITICAL: -Sy is required on Arch ISO as databases are not synced on boot
    pacman -Sy --needed --noconfirm tailscale || die "Failed to install tailscale."
fi

log_info "Starting Tailscale daemon..."
systemctl start tailscaled

log_info "Awaiting tailscaled IPC socket readiness..."
sock_timeout=15
declare -r TS_SOCKET="/run/tailscale/tailscaled.sock"

while [[ ! -S "$TS_SOCKET" ]] && (( sock_timeout > 0 )); do
    sleep 0.5
    ((sock_timeout--))
done

if [[ ! -S "$TS_SOCKET" ]]; then
    die "Tailscaled daemon started, but IPC socket ($TS_SOCKET) was not created."
fi
log_succ "Tailscale IPC socket is ready."

# Pure exit code validation
if tailscale status >/dev/null 2>&1; then
    log_succ "Tailscale is already authenticated."
else
    log_step "Authentication Required"
    
    while true; do
        printf "%sGenerating QR code for your friend...%s\n" "$C" "$W"
        
        if tailscale up --qr; then
            break
        else
            exit_code=$?
            printf "\n%s[WARN]%s Auth process failed (Code: %d).\n" "$Y" "$W" "$exit_code"
            printf "%s[QUESTION]%s Retry QR code (r), Switch to Link (l), or Quit (q)? [R/l/q] " "$Y" "$W"
            read -r retry_resp
            
            case "${retry_resp:-r}" in
                [Rr]*) continue ;;
                [Ll]*) tailscale up || die "Tailscale text authentication failed."; break ;;
                *) exit 1 ;;
            esac
        fi
    done
fi

log_info "Resolving Tailscale IP mapping..."
TS_IP=""
for _ in {1..10}; do
    TS_IP=$(tailscale ip -4 2>/dev/null || true)
    [[ -n "$TS_IP" ]] && break
    sleep 1
done

[[ -z "$TS_IP" ]] && die "VPN interface is up, but could not allocate a Tailscale IP."
log_succ "Tailscale IP: ${C}${TS_IP}${W}"

# --- Completion ---
log_step "Tailscale Setup Complete!"
printf "%sTailscale is Active!%s\n" "$G" "$W"
printf "   - IP Address: %s%s%s\n" "$C" "$TS_IP" "$W"
printf "   - Proceed to run the OpenSSH configuration script next.\n\n"
