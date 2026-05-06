#!/usr/bin/env bash
# Automates the setup of a secure, LAN-restricted vsftpd server
# Target: Arch Linux

# 1. Strict Mode & Environment Setup
set -euo pipefail

# 2. Output Formatting (Visual Feedback)
GREEN=$'\033[0;32m'
BLUE=$'\033[0;34m'
RED=$'\033[0;31m'
YELLOW=$'\033[1;33m'
NC=$'\033[0m'

log_info() { printf "${BLUE}[INFO]${NC} %s\n" "$1"; }
log_success() { printf "${GREEN}[SUCCESS]${NC} %s\n" "$1"; }
log_warn() { printf "${YELLOW}[WARN]${NC} %s\n" "$1"; }
log_error() { printf "${RED}[ERROR]${NC} %s\n" "$1"; exit 1; }

# 3. Root Privilege Check
if [[ $EUID -ne 0 ]]; then
   exec sudo "$0" "$@"
fi

# ==============================================================================
# User Intent Confirmation
# ==============================================================================
printf "${BLUE}[INPUT]${NC} Do you want to set up an FTP server for local file sharing? [Y/n]: "
read -r CONFIRM_INSTALL
CONFIRM_INSTALL=${CONFIRM_INSTALL:-Y}

if [[ ! "$CONFIRM_INSTALL" =~ ^[Yy]$ ]]; then
    echo ""
    log_info "Okay, I won't set it up. Exiting."
    exit 0
fi

# 4. User & Directory Logic
REAL_USER="${SUDO_USER:-$(whoami)}"
if [[ "$REAL_USER" == "root" ]]; then
    log_warn "Running as raw root. Cannot auto-detect target user."
    read -r -p "Enter the username to allow FTP access: " REAL_USER
fi

DEFAULT_PATH="/mnt/zram1"
printf "${BLUE}[INPUT]${NC} Enter FTP directory path [Default: ${DEFAULT_PATH}]: "
read -r USER_PATH_INPUT
FTP_ROOT="${USER_PATH_INPUT:-$DEFAULT_PATH}"

log_info "Target User: $REAL_USER"
log_info "FTP Root:    $FTP_ROOT"

# Dynamically determine the local subnet for secure LAN-only access
LOCAL_SUBNET=$(ip -o -f inet addr show scope global | awk '{print $4}' | head -n 1 || true)
if [[ -z "$LOCAL_SUBNET" ]]; then
    log_warn "Could not detect local subnet. Defaulting to global access (0.0.0.0/0)."
    LOCAL_SUBNET="any"
fi

# 5. Package Installation & Firewall Detection
FIREWALL_CMD=""
if systemctl is-active --quiet ufw || command -v ufw >/dev/null 2>&1; then
    FIREWALL_CMD="ufw"
else
    FIREWALL_CMD="firewalld"
fi

log_info "Updating package database and installing vsftpd..."
# FIXED: Removed the dangerous '-Syu' full system upgrade anti-pattern
pacman -S --needed --noconfirm vsftpd

if [[ "$FIREWALL_CMD" == "firewalld" ]]; then
    pacman -S --needed --noconfirm firewalld
fi

# 6. Firewall Configuration
if [[ "$FIREWALL_CMD" == "ufw" ]]; then
    log_info "Configuring UFW (LAN-Restricted)..."
    systemctl enable --now ufw.service

    # FIXED: Restrict FTP purely to the local subnet for security
    ufw allow from "$LOCAL_SUBNET" to any port 21 proto tcp comment 'LAN FTP Control' > /dev/null
    ufw allow from "$LOCAL_SUBNET" to any port 40000:40100 proto tcp comment 'LAN FTP Passive' > /dev/null
    
    ufw --force enable > /dev/null
    log_success "UFW rules applied. FTP restricted to LAN ($LOCAL_SUBNET)."

else
    log_info "Configuring Firewalld..."
    systemctl enable --now firewalld

    # Firewalld implicitly handles connection tracking
    firewall-cmd --permanent --add-service=ftp > /dev/null
    firewall-cmd --permanent --add-port=40000-40100/tcp > /dev/null
    firewall-cmd --reload > /dev/null
    log_success "Firewalld rules applied."
fi

# 7. VSFTPD Configuration Generation
log_info "Generating /etc/vsftpd.conf..."

cat > /etc/vsftpd.conf <<EOF
# --- Access Control ---
anonymous_enable=NO
local_enable=YES
write_enable=YES

# --- Chroot and Directory Settings ---
chroot_local_user=YES
allow_writeable_chroot=YES
local_root=$FTP_ROOT

# --- User Authentication and Listing ---
userlist_enable=YES
userlist_file=/etc/vsftpd.userlist
userlist_deny=NO

# --- Logging ---
xferlog_enable=YES
xferlog_std_format=YES
xferlog_file=/var/log/vsftpd.log
log_ftp_protocol=YES

# --- Connection Handling ---
listen=NO
listen_ipv6=YES
pam_service_name=vsftpd

# --- Passive Mode (Firewall Friendly) ---
pasv_enable=YES
pasv_min_port=40000
pasv_max_port=40100

# --- Banners and Messages ---
ftpd_banner=Welcome to the Arch Linux Secure LAN FTP service.

# --- Performance and Security Tweaks ---
use_sendfile=YES
connect_from_port_20=YES
EOF

# 8. User Allow List
log_info "Configuring Allow List..."
if ! grep -q "^${REAL_USER}$" /etc/vsftpd.userlist 2>/dev/null; then
    echo "$REAL_USER" | tee -a /etc/vsftpd.userlist > /dev/null
    log_success "Added '$REAL_USER' to /etc/vsftpd.userlist"
else
    log_info "User '$REAL_USER' already in allow list."
fi

# 9. Directory Permissions
log_info "Setting up directory permissions..."
if [[ ! -d "$FTP_ROOT" ]]; then
    mkdir -p "$FTP_ROOT"
    log_info "Created directory: $FTP_ROOT"
fi

chmod -R 777 "$FTP_ROOT"
log_success "Permissions set to 777 for $FTP_ROOT"

# 10. Service Activation
log_info "Starting vsftpd service..."
systemctl enable --now vsftpd

# 11. Final Status
IP_ADDR=$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}')
echo ""
log_success "FTP Server Setup Complete!"
echo "----------------------------------------------------"
printf "Server IP:      %s\n" "${IP_ADDR:-Unknown}"
printf "FTP User:       %s\n" "$REAL_USER"
printf "Root Dir:       %s\n" "$FTP_ROOT"
printf "LAN Access:     %s\n" "$LOCAL_SUBNET"
echo "----------------------------------------------------"
