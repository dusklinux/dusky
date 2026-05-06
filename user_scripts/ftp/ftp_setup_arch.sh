#!/usr/bin/env bash
# Automates the setup of a secure vsftpd server

# 1. Strict Mode & Environment Setup
set -euo pipefail

# 2. Output Formatting (Visual Feedback)
GREEN=$'\033[0;32m'
BLUE=$'\033[0;34m'
RED=$'\033[0;31m'
YELLOW=$'\033[1;33m'
NC=$'\033[0m' # No Color

log_info() { printf "${BLUE}[INFO]${NC} %s\n" "$1"; }
log_success() { printf "${GREEN}[SUCCESS]${NC} %s\n" "$1"; }
log_warn() { printf "${YELLOW}[WARN]${NC} %s\n" "$1"; }
log_error() { printf "${RED}[ERROR]${NC} %s\n" "$1"; exit 1; }

# 3. Root Privilege Check & Re-execution
if [[ $EUID -ne 0 ]]; then
   # We don't print "escalating" yet, strictly to keep the flow clean
   exec sudo "$0" "$@"
fi

# ==============================================================================
# NEW: User Intent Confirmation
# ==============================================================================
printf "${BLUE}[INPUT]${NC} Do you want to set up an FTP server for local file sharing? [Y/n]: "
read -r CONFIRM_INSTALL
# Default to 'Y' if enter is pressed
CONFIRM_INSTALL=${CONFIRM_INSTALL:-Y}

if [[ ! "$CONFIRM_INSTALL" =~ ^[Yy]$ ]]; then
    echo ""
    log_info "Okay, I won't set it up. Exiting."
    exit 0
fi
# ==============================================================================

# 4. User & Directory Logic
# Detect the actual user who invoked sudo (to add to allow list)
REAL_USER="${SUDO_USER:-$(whoami)}"

# If running as raw root (no sudo), ask for the user manually
if [[ "$REAL_USER" == "root" ]]; then
    log_warn "Running as raw root. Cannot auto-detect target user."
    read -r -p "Enter the username to allow FTP access: " REAL_USER
fi

# Interactive Directory Selection
DEFAULT_PATH="/mnt/zram1"
printf "${BLUE}[INPUT]${NC} Enter FTP directory path [Default: ${DEFAULT_PATH}]: "
read -r USER_PATH_INPUT
FTP_ROOT="${USER_PATH_INPUT:-$DEFAULT_PATH}"

log_info "Target User: $REAL_USER"
log_info "FTP Root:    $FTP_ROOT"

# 5. Package Installation & Firewall Detection
# Determine which firewall to use without breaking existing setups.
FIREWALL_CMD=""

if systemctl is-active --quiet ufw; then
    FIREWALL_CMD="ufw"
elif systemctl is-active --quiet firewalld; then
    FIREWALL_CMD="firewalld"
elif command -v ufw >/dev/null 2>&1; then
    # Neither is active, but UFW is installed
    FIREWALL_CMD="ufw"
else
    # Default original behavior: Assume/Install firewalld
    FIREWALL_CMD="firewalld"
fi

if [[ "$FIREWALL_CMD" == "ufw" ]]; then
    log_info "UFW detected as primary firewall framework."
    log_info "Updating system and installing dependencies (vsftpd)..."
    pacman -Syu --needed --noconfirm vsftpd
else
    log_info "Firewalld designated as primary firewall framework."
    log_info "Updating system and installing dependencies (vsftpd, firewalld)..."
    pacman -Syu --needed --noconfirm vsftpd firewalld
fi

# 6. Firewall Configuration
if [[ "$FIREWALL_CMD" == "ufw" ]]; then
    log_info "Configuring UFW..."
    # Ensure UFW systemd service is active before manipulating rules
    systemctl enable --now ufw.service

    # Add rules idempotently
    ufw allow 21/tcp > /dev/null
    ufw allow 40000:40100/tcp > /dev/null
    
    # Force enable bypasses the interactive "may disrupt ssh" warning
    ufw --force enable > /dev/null
    ufw reload > /dev/null
    log_success "Firewall rules applied via UFW (Port 21, 40000-40100)."

else
    log_info "Configuring Firewalld..."
    # Ensure service is running before using firewall-cmd
    systemctl enable --now firewalld

    # Add rules idempotently
    firewall-cmd --permanent --add-service=ftp > /dev/null
    firewall-cmd --permanent --add-port=40000-40100/tcp > /dev/null
    firewall-cmd --reload > /dev/null
    log_success "Firewall rules applied via Firewalld (Port 21, 40000-40100)."
fi

# 7. VSFTPD Configuration Generation
log_info "Generating /etc/vsftpd.conf..."

# Backup is not required per instructions ("Clean"), enforcing overwrite.
cat > /etc/vsftpd.conf <<EOF
# --- Access Control ---
# Allow anonymous FTP? (NO for security)
anonymous_enable=NO
# Allow local users to log in? (YES)
local_enable=YES
# Enable any form of write commands?
write_enable=YES

# --- Chroot and Directory Settings ---
# Restrict local users to their chroot jail after login.
chroot_local_user=YES
# Security Note: allow_writeable_chroot is enabled per user requirements.
allow_writeable_chroot=YES
# Specify the directory to which local users will be chrooted.
local_root=$FTP_ROOT

# --- User Authentication and Listing ---
# Enable the use of a userlist file.
userlist_enable=YES
# Path to the userlist file.
userlist_file=/etc/vsftpd.userlist
# When userlist_deny=NO, the userlist_file acts as an allow list.
userlist_deny=NO

# --- Logging ---
# Enable transfer logging.
xferlog_enable=YES
# Use standard log file format.
xferlog_std_format=YES
# Path to the vsftpd log file.
xferlog_file=/var/log/vsftpd.log
# Log all FTP protocol commands and responses.
log_ftp_protocol=YES

# --- Connection Handling ---
listen=NO
listen_ipv6=YES
pam_service_name=vsftpd

# --- Passive Mode ---
pasv_enable=YES
pasv_min_port=40000
pasv_max_port=40100

# --- Banners and Messages ---
ftpd_banner=Welcome to this Arch Linux FTP service.

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

# Applying specific instruction: chmod -R 777
chmod -R 777 "$FTP_ROOT"
log_success "Permissions set to 777 for $FTP_ROOT"

# 10. Service Activation
log_info "Starting vsftpd service..."
systemctl enable --now vsftpd

# 11. Final Status
IP_ADDR=$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K\S+')
echo ""
log_success "FTP Server Setup Complete!"
echo "----------------------------------------------------"
printf "Server IP:      %s\n" "${IP_ADDR:-Unknown}"
printf "FTP User:       %s\n" "$REAL_USER"
printf "Root Dir:       %s\n" "$FTP_ROOT"
printf "Logs:           /var/log/vsftpd.log\n"
echo "----------------------------------------------------"
