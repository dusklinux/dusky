#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Name:        Arch/Hyprland Remote Network Setup (Tailscale Only)
# Description: Automated setup for CGNAT-friendly remote access networking.
#              Strictly optimized for Arch Linux, Hyprland, and UWSM.
# Version:     3.1.0
# -----------------------------------------------------------------------------

# --- Strict Mode & Modernity ---
set -euo pipefail
shopt -s inherit_errexit

# --- Constants ---
declare -r SCRIPT_NAME="${0##*/}"
declare -r LOCKFILE="/var/lock/${SCRIPT_NAME}.lock"
declare -r NM_CONF_DIR="/etc/NetworkManager/conf.d"
declare -r MODULES_LOAD_DIR="/etc/modules-load.d"
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
    if [[ -f "$file" && ! -L "$file" ]]; then
        local backup="${file}.bak.$(date +%s)"
        cp -a "$file" "$backup"
        log_info "Backed up $file to $backup"
    fi
}

cmd_exists() { command -v "$1" &>/dev/null; }
svc_active() { systemctl is-active --quiet "$1" 2>/dev/null; }
pkg_installed() { pacman -Q "$1" &>/dev/null; }

# --- Pre-Flight Checks ---
(( BASH_VERSINFO[0] >= 5 )) || die "Bash 5.0+ required. Current: $BASH_VERSION"

if (( EUID != 0 )); then
    log_info "Escalating permissions..."
    exec sudo --preserve-env=TERM bash "$(realpath "${BASH_SOURCE[0]}")" "$@"
fi

[[ -f /etc/arch-release ]] || die "This script is strictly optimized for Arch Linux."

# Atomic Lock via File Descriptor (Prevents TOCTOU Race Condition)
exec 9> "$LOCKFILE"
flock -n 9 || die "Another instance is running."

# --- Main Logic ---
log_step "Initializing Setup..."
printf "This will configure Tailscale and System Networking Optimizations.\n"
printf "%sTarget Environment:%s Arch + Hyprland + UWSM\n\n" "$C" "$W"

printf "%s[QUESTION]%s Proceed? [Y/n] " "$Y" "$W"
read -r response
[[ "${response:-y}" =~ ^[Yy](es)?$ ]] || { log_info "Cancelled."; exit 0; }

# --- Phase -1: VPN Conflict Detection ---
log_step "Phase -1: Network Conflict Check"

# Optimized filtering: native awk anchor mapping
conflicting_vpns=$(ip -o link show | awk -F': ' '$2 ~ /^(tun|wg|ppp|CloudflareWARP|proton|nord)/ {print $2}' || true)

if [[ -n "$conflicting_vpns" ]]; then
    log_warn "Conflicting VPN interface(s) detected: ${R}${conflicting_vpns}${W}"
    printf "%s[QUESTION]%s Attempt to disconnect these VPNs automatically? [Y/n] " "$Y" "$W"
    read -r vpn_resp
    
    if [[ "${vpn_resp:-y}" =~ ^[Yy](es)?$ ]]; then
        if [[ "$conflicting_vpns" == *"CloudflareWARP"* ]] && cmd_exists warp-cli; then
            log_info "Attempting Cloudflare WARP disconnect..."
            warp-cli disconnect || log_warn "warp-cli returned error, proceeding anyway."
        fi

        for iface in $conflicting_vpns; do
            if ip link show "$iface" >/dev/null 2>&1; then
                log_info "Forcing interface $iface down..."
                ip link set dev "$iface" down || log_warn "Failed to bring down $iface"
            fi
        done
        log_succ "VPN cleanup routine finished."
    else
        log_warn "Proceeding with active VPNs. Routing conflicts are highly likely."
    fi
else
    log_succ "No conflicting VPNs detected."
fi

# --- Phase 0: System Foundation ---
log_step "Phase 0: System Foundation"

log_info "Configuring systemd-resolved..."
svc_active systemd-resolved || systemctl enable --now systemd-resolved

timeout=10
while [[ ! -f "$STUB_RESOLV" ]] && (( timeout > 0 )); do
    sleep 1; ((timeout--))
done

if [[ -f "$STUB_RESOLV" ]]; then
    backup_file "$RESOLV_CONF"
    ln -sf "$STUB_RESOLV" "$RESOLV_CONF"
    log_succ "DNS linked to systemd-resolved stub."
else
    log_warn "Could not locate stub-resolv.conf. DNS may require manual validation."
fi

if cmd_exists NetworkManager; then
    log_info "Hardening NetworkManager..."
    mkdir -p "$NM_CONF_DIR"
    cat > "${NM_CONF_DIR}/96-tailscale.conf" <<EOF
[keyfile]
unmanaged-devices=interface-name:tailscale0
EOF
    if svc_active NetworkManager; then
        systemctl reload NetworkManager || systemctl restart NetworkManager
    fi
    log_succ "NetworkManager instructed to ignore tailscale0."
fi

log_info "Configuring uinput module..."
mkdir -p "$MODULES_LOAD_DIR"
# Dedicated drop-in file is safer than appending to shared uinput.conf
echo "uinput" > "${MODULES_LOAD_DIR}/99-tailscale-uinput.conf"
modprobe uinput || log_warn "Failed to immediately modprobe uinput."
log_succ "uinput module persistence enabled."

if pkg_installed xdg-desktop-portal-wlr; then
    log_warn "Purging conflicting xdg-desktop-portal-wlr..."
    if pacman -Rns --noconfirm xdg-desktop-portal-wlr; then
        log_succ "Conflict eliminated."
    else
        log_warn "Failed to cleanly remove xdg-desktop-portal-wlr. Manual check advised."
    fi
fi

# --- Phase 1: Tailscale ---
log_step "Phase 1: Tailscale Network"

pkg_installed tailscale || { log_info "Installing Tailscale..."; pacman -S --needed --noconfirm tailscale; }

log_info "Restarting Tailscale daemon..."
systemctl restart tailscaled
systemctl enable tailscaled

# Mitigate IPC socket race condition
log_info "Awaiting tailscaled IPC socket readiness..."
sock_timeout=15
while ! tailscale status >/dev/null 2>&1 && (( sock_timeout > 0 )); do
    sleep 0.5
    ((sock_timeout--))
done

# Evaluate actual command state instead of just the timeout counter
if ! tailscale status >/dev/null 2>&1; then
    die "Tailscaled daemon started, but IPC socket is unresponsive."
fi
log_succ "Tailscale daemon is active and responding."

log_info "Applying firewall policies..."
if cmd_exists firewall-cmd && svc_active firewalld; then
    firewall-cmd --zone=trusted --add-interface=tailscale0 --permanent >/dev/null 2>&1 || true
    firewall-cmd --reload >/dev/null 2>&1 || true
    log_succ "Firewalld updated."
elif cmd_exists ufw && svc_active ufw; then
    ufw allow in on tailscale0 >/dev/null 2>&1 || true
    log_succ "UFW updated."
else
    log_info "No active firewall manager detected."
fi

# Pure exit code validation
if tailscale status >/dev/null 2>&1; then
    log_succ "Tailscale is already authenticated."
else
    log_step "Authentication Required"
    
    while true; do
        printf "%sGenerating QR code...%s\n" "$C" "$W"
        
        if tailscale up --qr; then
            break
        else
            # Exit handled gracefully by INT trap if user presses Ctrl+C
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
log_step "Setup Complete!"

printf "\n%s-------------------------------------------------------%s\n" "$C" "$W"
printf "             ELITE REMOTE NETWORK CONFIGURATION\n"
printf "%s-------------------------------------------------------%s\n\n" "$C" "$W"

printf "%sTailscale is Active!%s\n" "$G" "$W"
printf "   - IP Address: %s%s%s\n" "$C" "$TS_IP" "$W"
printf "   - Routable across the Tailnet.\n\n"

printf "%s[SUCCESS]%s Tunnel execution completed successfully.\n" "$G" "$W"
