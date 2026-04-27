#!/usr/bin/env bash
# ==============================================================================
# Script:  060_ufw_setup.sh
# Purpose: Installs and configures UFW for an Arch Linux workstation.
#          Optimized for Waydroid, Libvirt, Docker, Tailscale, VPNs, and SSH.
# Target:  Arch Linux (latest), Wayland/Hyprland
# Status:  PRODUCTION READY (Auto-Elevating, Strict-Mode Safe, Netfilter-Hardened)
# ==============================================================================

# --- 1. Safety & Path Resolution ---
set -euo pipefail
shopt -s inherit_errexit 2>/dev/null || true

SCRIPT_PATH=""
if [[ -f "$0" ]]; then
    SCRIPT_PATH=$(readlink -f "$0" 2>/dev/null) || SCRIPT_PATH=""
    if [[ -n "$SCRIPT_PATH" ]] && [[ ! -f "$SCRIPT_PATH" ]]; then
        SCRIPT_PATH=""
    fi
fi

# --- 2. Colors & Logging ---
if [[ -t 1 ]]; then
    readonly C_RESET=$'\e[0m' C_BOLD=$'\e[1m'
    readonly C_GREEN=$'\e[32m' C_BLUE=$'\e[34m'
    readonly C_YELLOW=$'\e[33m' C_RED=$'\e[31m'
else
    readonly C_RESET='' C_BOLD='' C_GREEN='' C_BLUE=''
    readonly C_YELLOW='' C_RED=''
fi

info()    { printf "%s[INFO]%s %s\n" "$C_BLUE" "$C_RESET" "$*"; }
success() { printf "%s[OK]%s   %s\n" "$C_GREEN" "$C_RESET" "$*"; }
warn()    { printf "%s[WARN]%s %s\n" "$C_YELLOW" "$C_RESET" "$*"; }
error()   { printf "%s[ERR]%s  %s\n" "$C_RED" "$C_RESET" "$*" >&2; }
die()     { error "$*"; exit 1; }

# --- 3. Privilege Escalation (Auto-Sudo) ---
if (( EUID != 0 )); then
    info "Administrative privileges required. Elevating via sudo..."
    if [[ -n "${SCRIPT_PATH:-}" && -f "$SCRIPT_PATH" ]]; then
        exec sudo --preserve-env=TERM,COLORTERM bash -- "$SCRIPT_PATH" "$@"
    elif [[ "${0##*/}" == "bash" || "${0##*/}" == "sh" ]]; then
        die "Cannot auto-elevate piped execution. Please run as root or download the script first."
    else
        exec sudo --preserve-env=TERM,COLORTERM bash -- "$0" "$@"
    fi
fi

printf "\n%sUFW Firewall Provisioning%s\n" "$C_BOLD" "$C_RESET"
printf "Provisions: Strict Routing · Docker Mitigation · Waydroid NAT · SSH · VPNs\n\n"

# --- 4. Installation ---
if ! pacman -Qi ufw &>/dev/null; then
    info "Installing UFW..."
    if [[ -f /var/lib/pacman/db.lck ]]; then
        die "Pacman database locked (/var/lib/pacman/db.lck). Is another pacman running?"
    fi
    pacman -S --noconfirm --needed ufw
    success "UFW installed."
else
    success "UFW is already installed."
fi

# --- 5. Network Detection ---
# Bulletproof detection: Excludes VPN/Tunnel interfaces to prevent poison routing
WAN_IFACE=$(ip -4 route show default 2>/dev/null | awk '/dev/ && $0 !~ /dev (wg|tun|tap|tailscale)/ {for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}' || true)

if [[ -z "$WAN_IFACE" ]]; then
    warn "Could not detect active physical WAN interface. Route forwarding may be skipped."
else
    info "Detected primary WAN interface: $WAN_IFACE"
fi

# --- 6. Sysctl Forwarding ---
info "Configuring kernel forwarding via sysctl..."
UFW_SYSCTL="/etc/ufw/sysctl.conf"

ensure_sysctl() {
    local key="$1"
    local file="$2"
    [[ ! -f "$file" ]] && touch "$file"
    if ! grep -q "^${key}=1" "$file"; then
        if grep -qE "^#?[[:space:]]*${key}[[:space:]]*=" "$file"; then
            sed -i -E "s|^#?[[:space:]]*${key}[[:space:]]*=.*|${key}=1|" "$file"
        else
            echo "${key}=1" >> "$file"
        fi
    fi
}

ensure_sysctl "net/ipv4/ip_forward" "$UFW_SYSCTL"
if [[ -f /proc/net/if_inet6 ]]; then
    ensure_sysctl "net/ipv6/conf/default/forwarding" "$UFW_SYSCTL"
    ensure_sysctl "net/ipv6/conf/all/forwarding" "$UFW_SYSCTL"
fi
success "Kernel IP forwarding enabled."

# --- 7. Strict Default Policies & Dynamic IPv6 ---
info "Enforcing strict default forward policies..."
UFW_DEFAULT="/etc/default/ufw"

if [[ -f "$UFW_DEFAULT" ]]; then
    if grep -qE "^DEFAULT_FORWARD_POLICY=\"ACCEPT\"" "$UFW_DEFAULT"; then
        sed -i 's/^DEFAULT_FORWARD_POLICY="ACCEPT"/DEFAULT_FORWARD_POLICY="DROP"/' "$UFW_DEFAULT"
        success "Reverted insecure DEFAULT_FORWARD_POLICY to DROP."
    fi
    
    # Kernel-Aware IPv6 Toggle to prevent ip6tables-restore crashes
    if [[ -f /proc/net/if_inet6 ]]; then
        if grep -q "^IPV6=no" "$UFW_DEFAULT"; then
            sed -i 's/^IPV6=no/IPV6=yes/' "$UFW_DEFAULT"
        fi
    else
        if grep -q "^IPV6=yes" "$UFW_DEFAULT"; then
            sed -i 's/^IPV6=yes/IPV6=no/' "$UFW_DEFAULT"
        fi
        warn "IPv6 is disabled in kernel. Dynamically set IPV6=no in UFW."
    fi
fi

# --- 8. Waydroid NAT Postrouting Injection ---
info "Injecting NAT masquerading for Waydroid..."
BEFORE_RULES="/etc/ufw/before.rules"

if [[ -f "$BEFORE_RULES" ]] && ! grep -q "# Waydroid NAT Integration" "$BEFORE_RULES"; then
    tmp_rules=$(mktemp)
    cat << 'EOF' > "$tmp_rules"
*nat
:POSTROUTING ACCEPT [0:0]
# Waydroid NAT Integration
-A POSTROUTING -s 192.168.240.0/24 -j MASQUERADE
-A POSTROUTING -s 192.168.250.0/24 -j MASQUERADE
COMMIT
EOF
    cat "$BEFORE_RULES" >> "$tmp_rules"
    mv "$tmp_rules" "$BEFORE_RULES"
    chmod 640 "$BEFORE_RULES"
    success "Injected *nat table into /etc/ufw/before.rules."
else
    success "Waydroid NAT already configured."
fi

# --- 9. Docker iptables Bypass Mitigation ---
info "Enforcing UFW state over Docker daemon..."

apply_docker_mitigation() {
    local rules_file="$1"
    [[ ! -f "$rules_file" ]] && return 0

    # Self-Healing: Scrub previously injected blocks precisely to prevent file bloat
    sed -i '/^# BEGIN DOCKER-USER MITIGATION/,/^# END DOCKER-USER MITIGATION/d' "$rules_file"

    local wan_drop_rule="# No WAN interface detected, skipping WAN drop"
    if [[ -n "$WAN_IFACE" ]]; then
        wan_drop_rule="-A DOCKER-USER -i $WAN_IFACE -j DROP"
    fi

    # CRITICAL: Ensure trailing newline exists before appending to prevent syntax corruption
    [ -n "$(tail -c1 "$rules_file")" ] && echo >> "$rules_file"

    cat << EOF >> "$rules_file"
# BEGIN DOCKER-USER MITIGATION
*filter
:DOCKER-USER - [0:0]
-A DOCKER-USER -i docker0 -j ACCEPT
-A DOCKER-USER -o docker0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
-A DOCKER-USER -i tailscale0 -j ACCEPT
-A DOCKER-USER -i waydroid0 -j ACCEPT
-A DOCKER-USER -i virbr0 -j ACCEPT
-A DOCKER-USER -i wg0 -j ACCEPT
-A DOCKER-USER -i tun+ -j ACCEPT
-A DOCKER-USER -i tap+ -j ACCEPT
$wan_drop_rule
-A DOCKER-USER -j RETURN
COMMIT
# END DOCKER-USER MITIGATION
EOF
}

apply_docker_mitigation "/etc/ufw/after.rules"
if [[ -f /proc/net/if_inet6 ]]; then
    apply_docker_mitigation "/etc/ufw/after6.rules"
    success "Appended hardened DOCKER-USER chain to IPv4 & IPv6 rules."
else
    success "Appended hardened DOCKER-USER chain to IPv4 rules (IPv6 skipped)."
fi

# --- 10. SSH Port Detection ---
SSH_PORT="22"
if systemctl is-enabled --quiet sshd.socket 2>/dev/null || systemctl is-active --quiet sshd.socket 2>/dev/null; then
    socket_port=$(systemctl cat sshd.socket 2>/dev/null | awk '
        /^[[:space:]]*ListenStream=/ {
            val = $0
            sub(/^[[:space:]]*ListenStream=/, "", val)
            gsub(/[[:space:]]/, "", val)
            if (val != "" && match(val, /[0-9]+$/)) { result = substr(val, RSTART, RLENGTH) }
        }
        END { if (result != "") print result }
    ' || true)
    [[ -n "$socket_port" && "$socket_port" =~ ^[0-9]+$ ]] && SSH_PORT="$socket_port"
else
    config_port=$(sshd -T 2>/dev/null | awk '/^port / {print $2; exit}' || true)
    [[ -n "$config_port" && "$config_port" =~ ^[0-9]+$ ]] && SSH_PORT="$config_port"
fi

# --- 11. Target Rulesets ---
info "Applying UFW Rulesets..."

ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow "${SSH_PORT}/tcp" comment 'OpenSSH' >/dev/null
ufw allow 41641/udp comment 'Tailscale Direct P2P' >/dev/null

TRUSTED_IFACES=(
    "tailscale0" # Tailscale mesh
    "waydroid0"  # Waydroid container
    "virbr0"     # Libvirt KVM bridge
    "docker0"    # Docker bridge
    "wg0"        # WireGuard default interface
    "tun0"       # OpenVPN default TUN interface
    "tap0"       # OpenVPN default TAP interface
)

for iface in "${TRUSTED_IFACES[@]}"; do
    ufw allow in on "$iface" comment "Trust IN: $iface" >/dev/null
    
    if [[ -n "$WAN_IFACE" ]]; then
        ufw route allow in on "$iface" out on "$WAN_IFACE" comment "Forward: $iface -> WAN" >/dev/null
    fi
done
success "Configured strict ingress & route forwarding rules."

# --- 12. Enable and Persist ---
info "Activating UFW..."

systemctl enable ufw.service >/dev/null 2>&1

# Fail-safe execution: Catches missing kernel logging modules (xt_LOG)
if ! ufw --force enable >/dev/null 2>&1; then
    warn "Activation failed ('Could not load logging rules')."
    info "Attempting fallback: Disabling UFW logging (missing netfilter modules?)..."
    ufw logging off >/dev/null 2>&1 || true
    
    if ! ufw --force enable >/dev/null; then
        die "UFW failed to activate entirely. Check your kernel netfilter support."
    else
        success "UFW activated successfully (Logging disabled as fallback)."
    fi
fi

# Fix: Restart Tailscale to re-inject netfilter chains wiped by UFW reload
if systemctl is-active --quiet tailscaled.service 2>/dev/null; then
    info "Restarting Tailscale to restore mesh netfilter state..."
    systemctl restart tailscaled.service
fi

if ufw status | grep -q "Status: active"; then
    printf "\n%s======================================================%s\n" "$C_GREEN" "$C_RESET"
    printf " %sUFW Configured and Active%s\n" "$C_BOLD" "$C_RESET"
    printf "%s======================================================%s\n" "$C_GREEN" "$C_RESET"
    ufw status verbose | awk '{print "  " $0}'
    printf "\n"
else
    die "UFW failed to activate. Check 'systemctl status ufw.service'."
fi

exit 0
