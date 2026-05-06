#!/usr/bin/env bash
# ==============================================================================
# Arch Linux SSH Bootstrap v5.1 (ISO Live Environment)
# ------------------------------------------------------------------------------
# Purpose: Auto-provision OpenSSH, patch PermitRootLogin, set root password,
#          detect Tailscale interface. Stripped for ephemeral overlayfs.
# Target:  Arch Linux ISO Live Environment
# ==============================================================================

# --- 1. Safety & Path Resolution ---
set -euo pipefail
shopt -s inherit_errexit 2>/dev/null || true

# --- 2. Colors & Logging ---
if [[ -t 1 ]]; then
    readonly C_RESET=$'\e[0m' C_BOLD=$'\e[1m'
    readonly C_GREEN=$'\e[32m' C_BLUE=$'\e[34m' C_YELLOW=$'\e[33m'
    readonly C_RED=$'\e[31m' C_CYAN=$'\e[36m' C_MAGENTA=$'\e[35m'
else
    readonly C_RESET='' C_BOLD='' C_GREEN='' C_BLUE=''
    readonly C_YELLOW='' C_RED='' C_CYAN='' C_MAGENTA=''
fi

info()    { printf "%s[INFO]%s %s\n" "$C_BLUE" "$C_RESET" "$*"; }
success() { printf "%s[OK]%s   %s\n" "$C_GREEN" "$C_RESET" "$*"; }
warn()    { printf "%s[WARN]%s %s\n" "$C_YELLOW" "$C_RESET" "$*"; }
error()   { printf "%s[ERR]%s  %s\n" "$C_RED" "$C_RESET" "$*" >&2; }
die()     { error "$*"; exit 1; }

cleanup() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]] && [[ $exit_code -ne 130 ]]; then
        printf "\n%s[!] Script exited with errors (code %d).%s\n" "$C_RED" "$exit_code" "$C_RESET" >&2
    fi
}
trap cleanup EXIT

# --- 3. Privilege Escalation ---
if [[ $EUID -ne 0 ]]; then
    die "Must run as root. This is the Arch ISO environment."
fi

# Hardcode user for ISO environment
readonly REAL_USER="root"

# --- 4. Package Installation ---
if ! pacman -Qi openssh &>/dev/null; then
    info "Syncing ephemeral DB and installing OpenSSH..."
    # CRITICAL: -Sy required for ISO
    if install_output=$(pacman -Sy --noconfirm --needed openssh 2>&1); then
        success "OpenSSH installed."
    else
        error "Installation failed:"
        printf "%s\n" "$install_output" >&2
        die "Failed to install openssh. Check internet connection."
    fi
else
    success "OpenSSH is already installed."
fi

# --- 5. Host Key Generation ---
info "Ensuring SSH host keys exist..."
ssh-keygen -A >/dev/null 2>&1 || true
success "SSH host keys verified."

# --- 6. Arch ISO Specific SSHD Patching ---
info "Patching /etc/ssh/sshd_config for Live Environment..."

# Arch ISO ships with PermitRootLogin prohibit-password. We must allow password login for the initial hook.
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
success "PermitRootLogin forced to 'yes'."

# --- 7. Force Root Password Setup ---
# SSH fundamentally rejects empty passwords. The Arch ISO root user has no password.
printf "\n%s[REQUIRED ACTION]%s\n" "$C_YELLOW" "$C_RESET"
info "You MUST set a temporary root password so your friend can SSH in."
while true; do
    if passwd root; then
        success "Temporary root password set."
        break
    else
        warn "Password setup failed or mismatched. Try again."
    fi
done
echo ""

# --- 8. Config Validation ---
if ! config_errors=$(sshd -t 2>&1); then
    error "sshd configuration is invalid:"
    printf "  %s\n" "$config_errors" >&2
    die "Fix /etc/ssh/sshd_config and re-run."
fi

# --- 9. Service Management ---
SSH_UNIT="sshd.service"
info "Starting $SSH_UNIT..."

# Start sshd directly in the live environment
systemctl start "$SSH_UNIT" >/dev/null 2>&1 || true

sshd_attempts=0
while [[ $sshd_attempts -lt 5 ]]; do
    if systemctl is-active --quiet "$SSH_UNIT" 2>/dev/null; then
        break
    fi
    ((sshd_attempts++)) || true
    sleep 1
done

if systemctl is-active --quiet "$SSH_UNIT" 2>/dev/null; then
    success "$SSH_UNIT is active."
else
    error "$SSH_UNIT failed to start."
    die "Check 'journalctl -xeu $SSH_UNIT' for details."
fi

# --- 10. Tailscale IP Detection ---
TARGET_IP="<IP-NOT-FOUND>"

if command -v tailscale &>/dev/null && systemctl is-active --quiet tailscaled 2>/dev/null; then
    TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || true)
    if [[ -n "$TAILSCALE_IP" ]]; then
        TARGET_IP="$TAILSCALE_IP"
        success "Tailscale routing detected."
    fi
else
    # Fallback LAN IP detection
    TARGET_IP=$(ip -o -4 addr show scope global 2>/dev/null | awk '$2 ~ /^(e|w)/ {split($4, a, "/"); print a[1]; exit}' || true)
fi

# --- 11. Final Output ---
CONN_CMD="ssh root@${TARGET_IP}"

printf "\n%s======================================================%s\n" "$C_GREEN" "$C_RESET"
printf " %sArch ISO SSH Setup Complete!%s\n" "$C_BOLD" "$C_RESET"
printf "%s======================================================%s\n" "$C_GREEN" "$C_RESET"
printf " %-15s : %s%s%s\n" "IP Address" "$C_CYAN" "$TARGET_IP" "$C_RESET"
printf " %-15s : %s%s%s\n" "Port" "$C_CYAN" "22" "$C_RESET"
printf " %-15s : %s%s%s\n" "User" "$C_CYAN" "root" "$C_RESET"
printf "\n Give your friend this command to connect:\n"
printf "    %s%s%s\n\n" "$C_MAGENTA" "$CONN_CMD" "$C_RESET"
printf " They will need the temporary password you just created.\n"
printf "%s======================================================%s\n" "$C_GREEN" "$C_RESET"

exit 0
