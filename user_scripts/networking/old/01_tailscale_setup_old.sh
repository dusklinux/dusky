#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Name:        Arch/Hyprland Remote Network Setup (Tailscale Only)
# Description: Automated setup for CGNAT-friendly remote access networking.
#              Optimized for Arch Linux, Hyprland, and UWSM.
# Version:     2.2.0
# -----------------------------------------------------------------------------

# --- Strict Mode & Safety ---
set -euo pipefail
# Ensure subshells inherit the ERR trap for strict error handling
shopt -s inherit_errexit 2>/dev/null || true

# --- Constants ---
declare -r SCRIPT_NAME="${0##*/}"
# Lockfile will be defined/created AFTER root check to avoid permission errors
declare -r LOCKFILE="/var/lock/${SCRIPT_NAME}.lock"
declare -r NM_CONF_DIR="/etc/NetworkManager/conf.d"
declare -r MODULES_LOAD_DIR="/etc/modules-load.d"
declare -r RESOLV_CONF="/etc/resolv.conf"
declare -r STUB_RESOLV="/run/systemd/resolve/stub-resolv.conf"
declare -r ORIGINAL_USER="${SUDO_USER:-}"

# Ensure TERM is set so TUI tools (like tailscale --qr) render correctly
export TERM=${TERM:-xterm-256color}

# --- Color Definitions (Safe for non-TTY) ---
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
    # Only try to remove lockfile if we are root (to avoid perm errors on exit)
    if (( EUID == 0 )); then
        rm -f "$LOCKFILE" 2>/dev/null || true
    fi
    
    if (( exit_code != 0 )); then
        printf "\n%s[FATAL]%s Script terminated with error code %d.\n" "$R" "$W" "$exit_code" >&2
    fi
}
trap cleanup EXIT INT TERM

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

# 1. Bash Version
if (( BASH_VERSINFO[0] < 5 )); then
    die "Bash 5.0+ required. Current: $BASH_VERSION"
fi

# 2. Privilege Escalation (MUST BE BEFORE LOCKFILE CHECK)
if (( EUID != 0 )); then
    log_info "Escalating permissions..."
    script_path=$(realpath "${BASH_SOURCE[0]}")
    # Preserve TERM to ensure TUI/QR codes render correctly inside sudo
    exec sudo --preserve-env=TERM bash "$script_path" "$@"
fi

# 3. Arch Check
if [[ ! -f /etc/arch-release ]]; then
    die "This script is optimized for Arch Linux only."
fi

# 4. Lock File (Safe now that we are root)
if [[ -f "$LOCKFILE" ]]; then
    if kill -0 "$(<"$LOCKFILE")" 2>/dev/null; then
        die "Another instance is running (PID $(<"$LOCKFILE"))."
    fi
fi
echo $$ > "$LOCKFILE"

# --- Main Logic ---

log_step "Initializing Setup..."
printf "This will configure Tailscale and System Networking Optimizations.\n"
printf "%sTarget Environment:%s Arch + Hyprland + UWSM\n\n" "$C" "$W"

printf "%s[QUESTION]%s Proceed? [Y/n] " "$Y" "$W"
read -r response
response=${response:-y} # Default to 'y'
[[ "${response,,}" =~ ^(y|yes)$ ]] || { log_info "Cancelled."; exit 0; }

# --- Phase -1: VPN Conflict Detection (CRITICAL) ---
log_step "Phase -1: Network Conflict Check"

# Detect active interfaces excluding loopback, ethernet, wifi, and tailscale itself
conflicting_vpns=$(ip -o link show | awk -F': ' '{print $2}' | grep -E '^(tun|wg|ppp|CloudflareWARP|proton|nord)' | grep -v 'tailscale0' || true)

if [[ -n "$conflicting_vpns" ]]; then
    log_warn "Conflicting VPN interface(s) detected: ${R}${conflicting_vpns}${W}"
    printf "   Tailscale cannot initialize if another VPN is routing traffic.\n"
    
    printf "%s[QUESTION]%s Attempt to disconnect these VPNs automatically? [Y/n] " "$Y" "$W"
    read -r vpn_resp
    vpn_resp=${vpn_resp:-y}
    
    if [[ "${vpn_resp,,}" =~ ^(y|yes)$ ]]; then
        # Specific handler for Cloudflare WARP
        if [[ "$conflicting_vpns" == *"CloudflareWARP"* ]]; then
            if cmd_exists warp-cli; then
                log_info "Detected Cloudflare WARP. Attempting disconnect via CLI..."
                warp-cli disconnect || log_warn "warp-cli disconnect returned error, proceeding anyway..."
            else
                log_warn "Cloudflare interface found but 'warp-cli' not in path."
            fi
        fi

        # Generic handler: shut down the link
        for iface in $conflicting_vpns; do
            if ip link show "$iface" >/dev/null 2>&1; then
                log_info "Forcing interface $iface down..."
                ip link set dev "$iface" down || log_warn "Failed to bring down $iface"
            fi
        done
        log_succ "VPN cleanup attempts finished."
    else
        log_warn "Proceeding with active VPNs. Tailscale setup may hang or fail."
    fi
else
    log_succ "No conflicting VPNs detected."
fi

# --- Phase 0: System Foundation ---
log_step "Phase 0: System Foundation"

# 1. DNS
log_info "Configuring systemd-resolved..."
if ! svc_active systemd-resolved; then
    systemctl enable --now systemd-resolved
fi

timeout=10
while [[ ! -f "$STUB_RESOLV" ]] && (( timeout > 0 )); do
    sleep 1; ((timeout--))
done

if [[ -f "$STUB_RESOLV" ]]; then
    backup_file "$RESOLV_CONF"
    ln -sf "$STUB_RESOLV" "$RESOLV_CONF"
    log_succ "DNS linked to systemd-resolved stub."
else
    log_warn "Could not find stub-resolv.conf. DNS might need manual check."
fi

# 2. NetworkManager
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
    log_succ "NetworkManager configured to ignore tailscale0."
fi

# 3. Uinput (Persistence) - Kept as foundational for future remote tools
log_info "Configuring uinput module..."
mkdir -p "$MODULES_LOAD_DIR"
if ! grep -q "^uinput$" "${MODULES_LOAD_DIR}/uinput.conf" 2>/dev/null; then
    echo "uinput" >> "${MODULES_LOAD_DIR}/uinput.conf"
fi
modprobe uinput || log_warn "Failed to modprobe uinput immediately."
log_succ "uinput configured."

# 4. Remove Conflicts - Kept as foundational for Wayland sessions
if pkg_installed xdg-desktop-portal-wlr; then
    log_warn "Removing conflicting xdg-desktop-portal-wlr..."
    pacman -Rns --noconfirm xdg-desktop-portal-wlr || true
    log_succ "Conflict removed."
fi

# --- Phase 1: Tailscale ---
log_step "Phase 1: Tailscale Network"

if ! pkg_installed tailscale; then
    log_info "Installing Tailscale..."
    pacman -S --needed --noconfirm tailscale
fi

log_info "Restarting Tailscale service to clear hung states..."
systemctl restart tailscaled
systemctl enable tailscaled
log_succ "Tailscale service restarted."

# Firewall
log_info "Applying firewall rules..."
if cmd_exists firewall-cmd && svc_active firewalld; then
    firewall-cmd --zone=trusted --add-interface=tailscale0 --permanent >/dev/null 2>&1 || true
    firewall-cmd --reload >/dev/null 2>&1 || true
    log_succ "Firewalld updated."
elif cmd_exists ufw && svc_active ufw; then
    ufw allow in on tailscale0 >/dev/null 2>&1 || true
    log_succ "UFW updated."
else
    log_info "No active firewall manager detected. Skipping."
fi

# Auth Loop
if tailscale status >/dev/null 2>&1; then
    log_succ "Tailscale is already authenticated."
else
    log_step "Authentication Required"
    
    # Retry loop for QR code
    while true; do
        printf "%sAttempting to generate QR code...%s\n" "$C" "$W"
        printf "Note: If this hangs, verify you are not on a restrictive VPN.\n\n"
        
        sleep 2
        
        # Try QR code logic
        if tailscale up --qr; then
            break # Success, break loop
        else
            exit_code=$?
            # If failed (likely Ctrl+C or timeout)
            printf "\n%s[WARN]%s Auth process interrupted or failed (Code: %d).\n" "$Y" "$W" "$exit_code"
            
            printf "%s[QUESTION]%s Retry QR code (r), Switch to Link (l), or Quit (q)? [R/l/q] " "$Y" "$W"
            read -r retry_resp
            retry_resp=${retry_resp:-r}
            
            case "${retry_resp,,}" in
                r|retry)
                    log_info "Retrying QR generation..."
                    continue
                    ;;
                l|link)
                    log_info "Switching to text link..."
                    tailscale up || die "Tailscale authentication failed."
                    break
                    ;;
                *)
                    log_info "Aborting authentication."
                    exit 1
                    ;;
            esac
        fi
    done
fi

# IP Validation Loop
log_info "Fetching Tailscale IP..."
TS_IP=""
for i in {1..10}; do
    TS_IP=$(tailscale ip -4 2>/dev/null || true)
    [[ -n "$TS_IP" ]] && break
    sleep 1
done

if [[ -z "$TS_IP" ]]; then
    die "Could not retrieve Tailscale IP. Is the VPN up?"
fi
log_succ "Tailscale IP: ${C}${TS_IP}${W}"

# --- Completion ---
log_step "Setup Complete!"

printf "\n%s-------------------------------------------------------%s\n" "$C" "$W"
printf "             ELITE REMOTE NETWORK CONFIGURATION\n"
printf "%s-------------------------------------------------------%s\n\n" "$C" "$W"

printf "%sTailscale is Active!%s\n" "$G" "$W"
printf "   - IP Address: %s%s%s\n" "$C" "$TS_IP" "$W"
printf "   - Use this IP to connect to this machine remotely.\n\n"

printf "%s[SUCCESS]%s Network tunnel configured successfully.\n" "$G" "$W"
