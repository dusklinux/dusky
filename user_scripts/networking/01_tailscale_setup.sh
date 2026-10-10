#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Name:        Arch/Hyprland Remote Network Setup (Tailscale Only)
# Description: Automated setup for CGNAT-friendly remote access networking.
#              Strictly optimized for Arch Linux, Hyprland, and UWSM.
# Version:     3.2.1
# -----------------------------------------------------------------------------

# --- Strict Mode & Modernity ---
set -euo pipefail
shopt -s inherit_errexit

# --- Constants ---
declare -r LOCKFILE="/run/lock/dusky-tailscale.lock"
declare -r NM_CONF_DIR="/etc/NetworkManager/conf.d"
declare -r RESOLV_CONF="/etc/resolv.conf"
declare -r STUB_RESOLV="/run/systemd/resolve/stub-resolv.conf"
declare -r NM_CONF="${NM_CONF_DIR}/96-tailscale.conf"

NM_TEMP=""

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
    [[ -z "$NM_TEMP" ]] || rm -f -- "$NM_TEMP"
    # Only report non-zero exits that aren't manual user interrupts (130/143)
    if (( exit_code != 0 && exit_code != 130 && exit_code != 143 )); then
        printf "\n%s[FATAL]%s Script terminated with error code %d.\n" "$R" "$W" "$exit_code" >&2
    fi
}

# Proper discrete signal handling
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

backup_file() {
    local file="$1"
    if [[ -e "$file" || -L "$file" ]]; then
        local backup
        backup="${file}.bak.$(date +%s%N)"
        cp -a "$file" "$backup"
        log_info "Backed up $file to $backup"
    fi
}

cmd_exists() { command -v "$1" &>/dev/null; }
svc_active() { systemctl is-active --quiet "$1" 2>/dev/null; }
pkg_installed() { pacman -Q "$1" &>/dev/null; }

# --- Pre-Flight Checks ---
if (( EUID != 0 )); then
    log_info "Escalating permissions..."
    script_path=$(realpath -- "${BASH_SOURCE[0]}")
    exec sudo --preserve-env=TERM bash "$script_path" "$@"
fi

[[ -f /etc/arch-release ]] || die "This script is strictly optimized for Arch Linux."

# Atomic Lock via File Descriptor (Prevents TOCTOU Race Condition)
exec 9> "$LOCKFILE"
flock -n 9 || die "Another networking setup or teardown is running."

# --- Main Logic ---
log_step "Initializing Setup..."
printf "This will configure Tailscale, systemd-resolved, and interface firewall rules.\n"
printf "%sTarget Environment:%s Arch + Hyprland + UWSM\n\n" "$C" "$W"

printf "%s[QUESTION]%s Proceed? [Y/n] " "$Y" "$W"
read -r response || { log_info "Cancelled (end of input)."; exit 0; }
[[ "${response:-y}" =~ ^[Yy](es)?$ ]] || { log_info "Cancelled."; exit 0; }

# --- Network Conflict Detection ---
log_step "Network Conflict Check"
# Other tunnels can coexist. Their presence alone does not prove a route conflict.
conflicting_vpns=$(ip -o link show up | awk -F': ' '$2 ~ /^(tun|wg|ppp|CloudflareWARP|proton|nord)/ {print $2}')
if [[ -n "$conflicting_vpns" ]]; then
    log_warn "Other active VPN interfaces detected: $conflicting_vpns. Check routing if connectivity fails."
fi

pkg_installed tailscale || { log_info "Installing Tailscale..."; pacman -S --needed --noconfirm tailscale; }

# --- Phase 0: System Foundation ---
log_step "Phase 0: System Foundation"

log_info "Configuring systemd-resolved..."
systemctl enable --now systemd-resolved

timeout=10
while [[ ! -f "$STUB_RESOLV" ]] && (( timeout > 0 )); do
    sleep 1
    ((timeout -= 1)) || :
done

if [[ -f "$STUB_RESOLV" ]]; then
    if [[ ! -L "$RESOLV_CONF" ]] || [[ "$(readlink -f -- "$RESOLV_CONF")" != "$STUB_RESOLV" ]]; then
        backup_file "$RESOLV_CONF"
        ln -sfnT "$STUB_RESOLV" "$RESOLV_CONF"
    fi
    log_succ "DNS linked to systemd-resolved stub."
else
    die "systemd-resolved did not create $STUB_RESOLV; DNS setup is incomplete."
fi

if cmd_exists NetworkManager; then
    log_info "Configuring NetworkManager..."
    mkdir -p "$NM_CONF_DIR"
    nm_config=$'[keyfile]\nunmanaged-devices+=interface-name:tailscale0'
    if [[ ! -f "$NM_CONF" ]] || [[ "$(< "$NM_CONF")" != "$nm_config" ]]; then
        backup_file "$NM_CONF"
        NM_TEMP=$(mktemp "${NM_CONF_DIR}/.96-tailscale.XXXXXX")
        printf '%s\n' "$nm_config" > "$NM_TEMP"
        chmod 644 "$NM_TEMP"
        mv -fT -- "$NM_TEMP" "$NM_CONF"
        NM_TEMP=""
    fi
    if svc_active NetworkManager; then
        nmcli general reload conf
    fi
    log_succ "NetworkManager instructed to ignore tailscale0."
fi

# --- Phase 1: Tailscale ---
log_step "Phase 1: Tailscale Network"

log_info "Ensuring Tailscale daemon is enabled and running..."
systemctl enable --now tailscaled

# The packaged Type=notify unit waits for daemon readiness. Always bring the
# connection up: status/IP output alone cannot distinguish a stopped connection.
log_step "Connect / Authenticate"
while true; do
    if tailscale up --qr; then
        break
    else
        exit_code=$?
        log_warn "Tailscale connection failed (code $exit_code)."
        printf "%s[QUESTION]%s Retry QR (r), use Link (l), or Quit (q)? [R/l/q] " "$Y" "$W"
        read -r retry_resp || exit 1
        case "${retry_resp:-r}" in
            [Rr]*) continue ;;
            [Ll]*) tailscale up || die "Tailscale connection failed."; break ;;
            *) exit 1 ;;
        esac
    fi
done

log_info "Applying firewall policies..."
if cmd_exists firewall-cmd && svc_active firewalld; then
    firewall-cmd --zone=trusted --change-interface=tailscale0 >/dev/null
    firewall-cmd --permanent --zone=trusted --change-interface=tailscale0 >/dev/null
    log_succ "Firewalld updated (runtime and permanent)."
fi
if cmd_exists ufw; then
    ufw_status=$(LC_ALL=C ufw status) || die "Could not inspect UFW status."
    if [[ "$ufw_status" == *"Status: active"* ]]; then
        ufw allow in on tailscale0
        log_succ "UFW updated."
    fi
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
log_step "Setup Complete!"

printf "\n%s-------------------------------------------------------%s\n" "$C" "$W"
printf "                 TAILSCALE NETWORK SETUP\n"
printf "%s-------------------------------------------------------%s\n\n" "$C" "$W"

printf "%sTailscale is Active!%s\n" "$G" "$W"
printf "   - IP Address: %s%s%s\n" "$C" "$TS_IP" "$W"
printf "   - Connected locally; remote access depends on Tailnet policy.\n\n"

printf "%s[SUCCESS]%s Tunnel execution completed successfully.\n" "$G" "$W"
