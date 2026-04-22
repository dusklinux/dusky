#!/usr/bin/env bash
# waydroid setup
# -----------------------------------------------------------------------------
# Name:        Arch Linux Waydroid Setup (Hyprland/UWSM Optimized)
# Description: Automates Waydroid installation, image setup, and optimization.
#              Handles kernel checks, direct ZIP extraction, and networking.
# Version:     2.6 (Feature: UFW & Firewalld Dual Support)
# -----------------------------------------------------------------------------

set -euo pipefail
# Bash 4.4+: Propagate failure in subshells to parent
shopt -s inherit_errexit 2>/dev/null || true

# --- Configuration & Colors ---
readonly C_RESET=$'\033[0m'
readonly C_GREEN=$'\033[1;32m'
readonly C_BLUE=$'\033[1;34m'
readonly C_RED=$'\033[1;31m'
readonly C_YELLOW=$'\033[1;33m'
readonly DEST_DIR="/etc/waydroid-extra/images"
readonly DEFAULT_SRC_DIR="/mnt/zram1"
readonly SERVICE_TIMEOUT=30

# State Tracking
IMAGES_UPDATED=0

# --- Helper Functions ---
log_info()    { printf "${C_BLUE}[INFO]${C_RESET} %s\n" "$*"; }
log_success() { printf "${C_GREEN}[OK]${C_RESET} %s\n" "$*"; }
log_warn()    { printf "${C_YELLOW}[WARN]${C_RESET} %s\n" "$*"; }
log_error()   { printf "${C_RED}[ERROR]${C_RESET} %s\n" "$*" >&2; exit 1; }

# Global cleanup trap
cleanup() {
    local exit_code=$?
    # Clean up temp dir if it was set and exists
    if [[ -n "${TEMP_DIR:-}" ]] && [[ -d "$TEMP_DIR" ]]; then
        rm -rf "$TEMP_DIR"
    fi
    exit "$exit_code"
}
trap cleanup EXIT

# --- 0. User Consent (Added per request) ---
echo ""
log_info "Waydroid is a hardware-accelerated Android emulator that runs smoothly."
log_info "NOTE: It requires that you MANUALLY download the images from SourceForge first."
read -r -p "Do you want to install Waydroid? [y/N] " _install_choice

# Default to No (if input is empty or anything other than y/Y)
if [[ ! "${_install_choice}" =~ ^[Yy]$ ]]; then
    log_info "Skipping Waydroid installation."
    exit 0
fi

# --- 1. Root Privilege Strategy ---
if [[ "${EUID}" -ne 0 ]]; then
    log_info "This script requires root privileges. Elevating..."
    exec sudo "$0" "$@"
fi

# Robustly detect the real user for AUR operations
if [[ -z "${SUDO_USER:-}" ]]; then
    log_error "Could not determine sudo user. Please run this script via 'sudo' from a normal user account."
fi
readonly REAL_USER="$SUDO_USER"
readonly REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)

log_info "Running as root. AUR operations will be performed as user: $REAL_USER"

# --- 2. Kernel & Module Verification ---
log_info "Verifying Kernel and Modules..."

CURRENT_KERNEL=$(uname -r)
if [[ ! "$CURRENT_KERNEL" =~ (arch|zen|lts) ]]; then
    log_warn "Detected kernel '$CURRENT_KERNEL' is not standard Arch (linux, linux-lts, linux-zen)."
    log_warn "If modules are missing, please refer to the Arch Wiki Binder setup."
fi

# Check modules (Check /proc/filesystems)
if grep -qE "binder|ashmem" /proc/filesystems; then
    log_success "Binder and Ashmem modules detected."
else
    log_warn "Binder/Ashmem not explicitly found in /proc/filesystems."
    log_warn "Attempting to load modules..."
    modprobe binder_linux 2>/dev/null || true
    modprobe ashmem_linux 2>/dev/null || true
    
    # Re-check via lsmod as fallback if /proc/filesystems doesn't update immediately
    if lsmod | grep -qE "^binder_linux|^ashmem_linux"; then
         log_success "Modules loaded successfully."
    elif grep -qE "binder|ashmem" /proc/filesystems; then
         log_success "Modules detected in /proc."
    else
         log_error "Modules missing. Please install a compatible kernel (linux-zen recommended) or binder_linux-dkms."
    fi
fi

# --- 3. Package Installation (AUR) ---
if ! command -v waydroid &>/dev/null; then
    log_info "Waydroid not found. Installing..."
    
    AUR_HELPER=""
    # FIX: Use 'bash -c' to run 'command -v' because 'command' is a shell builtin.
    if runuser -u "$REAL_USER" -- bash -c "command -v paru" &>/dev/null; then
        AUR_HELPER="paru"
    elif runuser -u "$REAL_USER" -- bash -c "command -v yay" &>/dev/null; then
        AUR_HELPER="yay"
    else
        log_error "Neither 'paru' nor 'yay' found. Please install 'paru' to proceed."
    fi
    
    log_info "Installing waydroid using $AUR_HELPER..."
    runuser -u "$REAL_USER" -- "$AUR_HELPER" -S --noconfirm --needed waydroid
else
    log_success "Waydroid package is already installed."
fi

# --- 4. Image Handling Logic ---
log_info "Preparing Waydroid Images..."

# Check if images already exist to potentially skip the prompt
if [[ -f "$DEST_DIR/system.img" ]] && [[ -f "$DEST_DIR/vendor.img" ]]; then
    log_info "Existing images detected in $DEST_DIR."
else
    printf "\n${C_YELLOW}--- MANUAL DOWNLOAD REQUIRED ---${C_RESET}\n"
    printf "1. System: https://sourceforge.net/projects/waydroid/files/images/system/lineage/waydroid_x86_64/\n"
    printf "   - Choose Lineage 18.1 OR 20 (Must match Vendor version!)\n"
    printf "   - Choose VANILLA (Plain) or GAPPS (Play Store)\n"
    printf "2. Vendor: https://sourceforge.net/projects/waydroid/files/images/vendor/waydroid_x86_64/\n"
    printf "   - Download Vendor ZIP (Match the Lineage version!).\n\n"
fi

read -r -p "Do you have the System and Vendor files downloaded? [y/N] " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    if [[ -f "$DEST_DIR/system.img" ]]; then
        log_info "Skipping download step (using existing installed images)."
    else
        log_info "Please download the files and restart the script."
        exit 0
    fi
else
    # Only ask for path if user says they have files
    read -r -e -p "Enter directory containing downloaded files [Default: $DEFAULT_SRC_DIR]: " INPUT_SRC_DIR
    INPUT_SRC_DIR="${INPUT_SRC_DIR/#\~/$REAL_HOME}"
    SRC_DIR="${INPUT_SRC_DIR:-$DEFAULT_SRC_DIR}"

    if [[ ! -d "$SRC_DIR" ]]; then
        log_error "Directory $SRC_DIR does not exist."
    fi

    # Find files (Corrected Precedence with Grouping)
    SYSTEM_FILE=$(find "$SRC_DIR" -maxdepth 1 \( -name "*system*.zip" -o -name "system.img" \) | head -n 1)
    VENDOR_FILE=$(find "$SRC_DIR" -maxdepth 1 \( -name "*vendor*.zip" -o -name "vendor.img" \) | head -n 1)

    if [[ -z "$SYSTEM_FILE" ]]; then
        read -r -e -p "System image/zip not auto-detected. Enter full path: " SYSTEM_FILE
        SYSTEM_FILE="${SYSTEM_FILE/#\~/$REAL_HOME}"
    fi
    if [[ -z "$VENDOR_FILE" ]]; then
        read -r -e -p "Vendor image/zip not auto-detected. Enter full path: " VENDOR_FILE
        VENDOR_FILE="${VENDOR_FILE/#\~/$REAL_HOME}"
    fi

    [[ -f "$SYSTEM_FILE" ]] || log_error "System file not found: $SYSTEM_FILE"
    [[ -f "$VENDOR_FILE" ]] || log_error "Vendor file not found: $VENDOR_FILE"

    log_info "Detected:"
    echo "   System: $SYSTEM_FILE"
    echo "   Vendor: $VENDOR_FILE"

    mkdir -p "$DEST_DIR"

    # Function to process files with Smart Overwrite Check
    process_image() {
        local input="$1"
        local output_name="$2"
        local dest_path="$DEST_DIR/$output_name"

        # Check if destination exists and is not empty
        if [[ -s "$dest_path" ]]; then
            log_warn "Destination exists: $dest_path"
            read -r -p "Overwrite? [y/N] " ow
            if [[ ! "$ow" =~ ^[Yy]$ ]]; then
                log_info "Skipping $output_name extraction."
                return 0
            fi
        fi

        # Mark that we are changing images
        IMAGES_UPDATED=1

        if [[ "$input" == *.zip ]]; then
            log_info "Streaming extraction of $(basename "$input") directly to $dest_path..."
            
            local internal_img
            internal_img=$(unzip -Z -1 "$input" | grep -F "$output_name" | head -n 1)
            
            if [[ -z "$internal_img" ]]; then
                 internal_img=$(unzip -Z -1 "$input" | grep -F ".img" | head -n 1)
            fi

            if [[ -z "$internal_img" ]]; then
                log_error "Could not find an .img file inside $input"
            fi

            unzip -p "$input" "$internal_img" > "$dest_path"
            log_success "Extracted."

        elif [[ "$input" == *.img ]]; then
            read -r -p "For $(basename "$input"): (k)eep original or (m)ove to save space? [k/m] " action
            if [[ "$action" =~ ^[Mm]$ ]]; then
                mv "$input" "$dest_path"
                log_success "Moved."
            else
                cp "$input" "$dest_path"
                log_success "Copied."
            fi
        fi
    }

    process_image "$SYSTEM_FILE" "system.img"
    process_image "$VENDOR_FILE" "vendor.img"
fi

# --- 5. Initialization ---
# Smart Init: Only run 'waydroid init' if images changed OR waydroid isn't set up
if [[ $IMAGES_UPDATED -eq 1 ]] || [[ ! -f "/var/lib/waydroid/images/system.img" ]]; then
    log_info "Initializing Waydroid (Forcing manual images)..."
    waydroid init -f -i "$DEST_DIR"
else
    log_success "Waydroid is already initialized and images were not changed. Skipping 'init'."
fi

# --- 6. Service Management (Systemd) ---
log_info "Enabling Waydroid container service..."
systemctl enable --now waydroid-container

log_info "Waiting for Waydroid container to become active..."
elapsed=0
while (( elapsed < SERVICE_TIMEOUT )); do
    if systemctl is-active --quiet waydroid-container; then
        log_success "Waydroid Container is running."
        break
    fi
    sleep 1
    ((elapsed++))
done

if ! systemctl is-active --quiet waydroid-container; then
    log_error "Waydroid Container failed to start within ${SERVICE_TIMEOUT}s. Check 'systemctl status waydroid-container'."
fi

# --- 7. Fixes & Optimizations ---

# Multi-window Support
log_info "Setting multi-window property..."
sleep 2 
if waydroid prop set persist.waydroid.multi_windows true; then
    log_success "Multi-window enabled."
else
    log_warn "Failed to set multi-window property. Is the session running?"
fi

# Network Fixes
log_info "Checking Networking..."

# 7a. IP Forwarding
if [[ "$(sysctl -n net.ipv4.ip_forward)" -eq 0 ]]; then
    log_warn "IP Forwarding is disabled. Enabling..."
    echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/99-waydroid.conf
    sysctl -p /etc/sysctl.d/99-waydroid.conf
    log_success "IP Forwarding enabled."
fi

# 7b. Firewall (DUAL SUPPORT: Firewalld & UFW)
if systemctl is-active --quiet firewalld; then
    log_info "Firewalld detected. Applying rules..."
    firewall-cmd --zone=trusted --add-interface=waydroid0 --permanent >/dev/null
    firewall-cmd --zone=trusted --add-masquerade --permanent >/dev/null
    firewall-cmd --reload >/dev/null
    log_success "Firewalld rules applied."
elif command -v ufw &>/dev/null && systemctl is-active --quiet ufw; then
    log_info "UFW detected. Applying rules..."
    # Allow inbound traffic from the container to the host (for ADB, local DNS, etc.)
    ufw allow in on waydroid0 >/dev/null 2>&1 || true
    # Allow the container traffic to be forwarded/routed to the internet
    ufw route allow in on waydroid0 >/dev/null 2>&1 || true
    log_success "UFW rules applied."
else
    log_info "No active firewall manager detected. If you have network issues, check raw iptables/nftables."
fi

# 7c. Permission Fixes
if [[ -n "${SRC_DIR:-}" ]] && [[ -d "$SRC_DIR" ]]; then
    # Smart Perms: Only ask if permissions look wrong (simple check: not world writable)
    # Actually, stat check is messy, let's just ask but inform user it might be redundant
    log_info "Checking permissions for shared folder ($SRC_DIR)..."
    read -r -p "Re-apply 'chmod 777 -R' to $SRC_DIR? (Say N if already done) [y/N] " perm_confirm
    if [[ "$perm_confirm" =~ ^[Yy]$ ]]; then
        chmod 777 -R "$SRC_DIR"
        log_success "Permissions set to 777."
    else
        log_info "Skipping permission changes."
    fi
fi

# --- 8. ARM Translation (Libhoudini) ---
printf "\n${C_BLUE}--- ARM Translation (Libhoudini) & Magisk ---${C_RESET}\n"
read -r -p "Run casualsnek's waydroid_script (Libhoudini/Magisk)? [y/N] " run_script

if [[ "$run_script" =~ ^[Yy]$ ]]; then
    log_info "Setting up waydroid_script..."
    
    # Create temp dir
    TEMP_DIR=$(mktemp -d)
    
    if ! command -v git &>/dev/null; then
         log_info "Installing Git..."
         pacman -S --noconfirm --needed git
    fi
    
    git clone https://github.com/casualsnek/waydroid_script "$TEMP_DIR"
    
    log_info "Setting up Python Virtual Environment to install dependencies..."
    if ! python3 -m venv "$TEMP_DIR/venv"; then
        log_error "Failed to create python venv. Ensure 'python' is installed."
    fi
    
    log_info "Installing Python dependencies (InquirerPy, tqdm)..."
    "$TEMP_DIR/venv/bin/pip" install -U pip >/dev/null 2>&1
    if ! "$TEMP_DIR/venv/bin/pip" install -r "$TEMP_DIR/requirements.txt" >/dev/null; then
        log_error "Failed to install dependencies via pip."
    fi
    
    log_info "Starting interactive script (Running as Root)..."
    (cd "$TEMP_DIR" && "$TEMP_DIR/venv/bin/python" main.py)
fi

# --- Conclusion ---
printf "\n${C_GREEN}===========================================${C_RESET}\n"
printf "${C_GREEN}   Waydroid Setup Complete! ${C_RESET}\n"
printf "${C_GREEN}===========================================${C_RESET}\n"
printf "1. Reboot your system (if kernel modules were just installed).\n"
printf "2. Launch Waydroid: waydroid session start\n"
printf "===========================================\n"
