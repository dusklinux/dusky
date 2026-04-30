#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Name:        Arch Linux Waydroid Setup (Hyprland/UWSM Optimized)
# Description: Automates Waydroid installation, image setup, and optimization.
#              Zero-legacy code. Optimized for Bash 5+ & Wayland Native.
# Version:     5.2 (Production Final - Strict Mode Arithmetic Hardened)
# -----------------------------------------------------------------------------

set -euo pipefail
shopt -s inherit_errexit 2>/dev/null || true

# --- Configuration & Colors ---
readonly C_RESET=$'\033[0m'
readonly C_GREEN=$'\033[1;32m'
readonly C_BLUE=$'\033[1;34m'
readonly C_RED=$'\033[1;31m'
readonly C_YELLOW=$'\033[1;33m'
readonly DEST_DIR="/etc/waydroid-extra/images"
readonly SERVICE_TIMEOUT=30
readonly TMP_SUDOERS="/etc/sudoers.d/99-waydroid-setup-temp"

# State Tracking
IMAGES_UPDATED=0

# --- Helper Functions ---
log_info()    { printf "${C_BLUE}[INFO]${C_RESET} %s\n" "$*"; }
log_success() { printf "${C_GREEN}[OK]${C_RESET} %s\n" "$*"; }
log_warn()    { printf "${C_YELLOW}[WARN]${C_RESET} %s\n" "$*"; }
log_error()   { printf "${C_RED}[ERROR]${C_RESET} %s\n" "$*" >&2; exit 1; }

cleanup() {
    local exit_code=$?
    # Strip the temporary NOPASSWD rule immediately upon exit
    if [[ -f "$TMP_SUDOERS" ]]; then
        rm -f "$TMP_SUDOERS"
    fi
    if [[ -n "${TEMP_DIR:-}" ]] && [[ -d "$TEMP_DIR" ]]; then
        rm -rf "$TEMP_DIR"
    fi
    exit "$exit_code"
}
trap cleanup EXIT

# --- 0. Smart User Consent ---
# Prevents the double-prompt when the script re-executes itself via sudo
if [[ -z "${WAYDROID_SETUP_RUNNING:-}" ]]; then
    echo ""
    log_info "Waydroid is a hardware-accelerated Android environment."
    log_info "NOTE: Requires MANUAL image downloads from SourceForge first."
    read -r -p "Proceed with Waydroid installation? [y/N] " _install_choice

    if [[ ! "${_install_choice}" =~ ^[Yy]$ ]]; then
        log_info "Skipping Waydroid installation."
        exit 0
    fi
fi

# --- 1. Root Privilege & User Strategy ---
if [[ "${EUID}" -ne 0 ]]; then
    log_info "Elevating to root privileges..."
    exec sudo WAYDROID_SETUP_RUNNING=1 "$0" "$@"
fi

# Safely extract the invoking user to drop privileges for AUR/Makepkg tasks
REAL_USER="${SUDO_USER:-${DOAS_USER:-$(logname 2>/dev/null || echo "")}}"
if [[ -z "$REAL_USER" ]] || [[ "$REAL_USER" == "root" ]]; then
    log_error "Could not determine normal user. Run this script via 'sudo' from your standard user account to permit AUR operations."
fi
readonly REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)

log_info "Running as root. AUR operations scoped to: $REAL_USER"

# Create a temporary sudoers rule to prevent AUR helpers from double-prompting for passwords
echo "${REAL_USER} ALL=(ALL) NOPASSWD: /usr/bin/pacman" > "$TMP_SUDOERS"
chmod 0440 "$TMP_SUDOERS"

# --- 2. Kernel Module & Core Utility Verification ---
log_info "Verifying core dependencies and kernel modules..."

# Install strictly required host utilities
pacman -S --noconfirm --needed git unzip lzip squashfs-tools

# Waydroid utilizes memfd natively on modern kernels; ashmem is obsolete.
if grep -qE "binder" /proc/filesystems; then
    log_success "BinderFS detected natively."
else
    log_warn "Binder not explicitly found in /proc/filesystems. Probing module..."
    modprobe binder_linux 2>/dev/null || true
    
    if lsmod | grep -qE "^binder_linux"; then
         log_success "binder_linux module loaded."
    else
         log_error "Binder missing. Ensure you are running linux-zen or have binder_linux-dkms installed."
    fi
fi

# --- 3. Package Installation (AUR) ---
if ! command -v waydroid &>/dev/null; then
    log_info "Waydroid missing. Initiating AUR install..."
    
    AUR_HELPER=""
    if sudo -u "$REAL_USER" bash -c "command -v paru" &>/dev/null; then
        AUR_HELPER="paru"
    elif sudo -u "$REAL_USER" bash -c "command -v yay" &>/dev/null; then
        AUR_HELPER="yay"
    else
        log_error "Neither 'paru' nor 'yay' found. Install an AUR helper to proceed."
    fi
    
    log_info "Deploying waydroid using $AUR_HELPER..."
    sudo -u "$REAL_USER" "$AUR_HELPER" -S --noconfirm --needed waydroid
else
    log_success "Waydroid core is already present."
fi

# --- 4. Image Handling & Smart Pathing ---
log_info "Preparing Waydroid Images..."

if [[ -f "$DEST_DIR/system.img" ]] && [[ -f "$DEST_DIR/vendor.img" ]]; then
    log_info "Existing images mapped in $DEST_DIR."
else
    printf "\n${C_YELLOW}--- MANUAL DOWNLOAD REQUIRED ---${C_RESET}\n"
    printf "1. System: https://sourceforge.net/projects/waydroid/files/images/system/lineage/waydroid_x86_64/\n"
    printf "2. Vendor: https://sourceforge.net/projects/waydroid/files/images/vendor/waydroid_x86_64/\n\n"
fi

read -r -p "Are System and Vendor files downloaded? [y/N] " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    if [[ -f "$DEST_DIR/system.img" ]]; then
        log_info "Skipping download prompt (existing assets found)."
    else
        log_info "Download required assets and re-run."
        exit 0
    fi
else
    # Dynamic Search Path Logic
    SEARCH_PATHS=(
        "$REAL_HOME/Downloads/Waydroid"
        "$REAL_HOME/Downloads"
        "/mnt/zram1"
    )
    
    DETECTED_SRC=""
    for p in "${SEARCH_PATHS[@]}"; do
        if [[ -d "$p" ]]; then
            if find "$p" -maxdepth 1 -type f \( -name "*system*.zip" -o -name "system.img" \) | grep -q .; then
                DETECTED_SRC="$p"
                break
            fi
        fi
    done
    
    DEFAULT_SRC_DIR="${DETECTED_SRC:-/mnt/zram1}"

    read -r -e -p "Enter directory containing downloaded files [Default: $DEFAULT_SRC_DIR]: " INPUT_SRC_DIR
    INPUT_SRC_DIR="${INPUT_SRC_DIR/#\~/$REAL_HOME}"
    SRC_DIR="${INPUT_SRC_DIR:-$DEFAULT_SRC_DIR}"

    [[ ! -d "$SRC_DIR" ]] && log_error "Directory $SRC_DIR does not exist."

    # Robust file detection via mapfile
    mapfile -t sys_files < <(find "$SRC_DIR" -maxdepth 1 \( -name "*system*.zip" -o -name "system.img" \))
    mapfile -t ven_files < <(find "$SRC_DIR" -maxdepth 1 \( -name "*vendor*.zip" -o -name "vendor.img" \))

    SYSTEM_FILE="${sys_files[0]:-}"
    VENDOR_FILE="${ven_files[0]:-}"

    if [[ -z "$SYSTEM_FILE" ]]; then
        read -r -e -p "System archive not auto-detected. Full path: " SYSTEM_FILE
        SYSTEM_FILE="${SYSTEM_FILE/#\~/$REAL_HOME}"
    fi
    if [[ -z "$VENDOR_FILE" ]]; then
        read -r -e -p "Vendor archive not auto-detected. Full path: " VENDOR_FILE
        VENDOR_FILE="${VENDOR_FILE/#\~/$REAL_HOME}"
    fi

    [[ -f "$SYSTEM_FILE" ]] || log_error "Target missing: $SYSTEM_FILE"
    [[ -f "$VENDOR_FILE" ]] || log_error "Target missing: $VENDOR_FILE"

    log_info "Targets acquired:"
    echo "   System: $SYSTEM_FILE"
    echo "   Vendor: $VENDOR_FILE"

    mkdir -p "$DEST_DIR"

    process_image() {
        local input="$1"
        local output_name="$2"
        local dest_path="$DEST_DIR/$output_name"

        if [[ -s "$dest_path" ]]; then
            log_warn "Asset exists: $dest_path"
            read -r -p "Overwrite? [y/N] " ow
            if [[ ! "$ow" =~ ^[Yy]$ ]]; then
                log_info "Skipping $output_name."
                return 0
            fi
        fi

        IMAGES_UPDATED=1

        if [[ "$input" == *.zip ]]; then
            log_info "Streaming $(basename "$input") to $dest_path..."
            
            local internal_img
            internal_img=$(unzip -Z -1 "$input" | grep -F "$output_name" | head -n 1)
            
            if [[ -z "$internal_img" ]]; then
                 internal_img=$(unzip -Z -1 "$input" | grep -F ".img" | head -n 1)
            fi

            [[ -z "$internal_img" ]] && log_error "No internal .img found in $input"

            unzip -p "$input" "$internal_img" > "$dest_path"
            log_success "Extraction complete."

        elif [[ "$input" == *.img ]]; then
            read -r -p "For $(basename "$input"): (k)eep original or (m)ove to free space? [k/m] " action
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

# --- 5. Container Initialization ---
if [[ $IMAGES_UPDATED -eq 1 ]] || [[ ! -f "/var/lib/waydroid/images/system.img" ]]; then
    log_info "Initializing container filesystem..."
    waydroid init -f -i "$DEST_DIR"
else
    log_success "Container initialized and parity maintained. Skipping 'init'."
fi

# --- 6. Systemd Orchestration ---
log_info "Deploying Waydroid systemd container..."
systemctl enable --now waydroid-container

log_info "Polling container telemetry..."
elapsed=0
while (( elapsed < SERVICE_TIMEOUT )); do
    if systemctl is-active --quiet waydroid-container; then
        log_success "Container telemetry active."
        break
    fi
    sleep 1
    # FIX: Pre-increment to prevent set -e termination on math evaluating to 0
    ((++elapsed))
done

if ! systemctl is-active --quiet waydroid-container; then
    log_error "Container boot timeout. Inspect 'systemctl status waydroid-container'."
fi

# --- 7. UWSM/Hyprland Optimizations & Networking ---
log_info "Injecting Wayland/Multi-window parameters..."

PROP_FILE="/var/lib/waydroid/waydroid_base.prop"
if [[ -f "$PROP_FILE" ]]; then
    if grep -q "persist.waydroid.multi_windows=" "$PROP_FILE"; then
        sed -i 's/persist.waydroid.multi_windows=.*/persist.waydroid.multi_windows=true/' "$PROP_FILE"
    else
        echo "persist.waydroid.multi_windows=true" >> "$PROP_FILE"
    fi
    log_success "Multi-window forced natively."
else
    log_warn "Base prop file missing. Multi-window must be set via UI later."
fi

log_info "Auditing Networking Stack..."

# IP Forwarding
if [[ "$(sysctl -n net.ipv4.ip_forward)" -eq 0 ]]; then
    echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/99-waydroid.conf
    sysctl -p /etc/sysctl.d/99-waydroid.conf
    log_success "IPv4 forwarding activated."
fi

# Poll for waydroid0 interface
log_info "Waiting for network bridge topology..."
net_elapsed=0
while ! ip link show waydroid0 &>/dev/null && (( net_elapsed < 10 )); do
    sleep 1
    # FIX: Pre-increment to prevent set -e termination on math evaluating to 0
    ((++net_elapsed))
done

# Docker Iptables Mitigation
if command -v docker &>/dev/null && command -v iptables &>/dev/null; then
    iptables -C FORWARD -i waydroid0 -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -i waydroid0 -j ACCEPT
    iptables -C FORWARD -o waydroid0 -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -o waydroid0 -j ACCEPT
    log_success "Docker iptables FORWARD drop mitigations applied."
fi

# Routing & Firewall Configuration
if systemctl is-active --quiet firewalld; then
    firewall-cmd --zone=trusted --add-interface=waydroid0 --permanent >/dev/null
    firewall-cmd --zone=trusted --add-masquerade --permanent >/dev/null
    firewall-cmd --reload >/dev/null
    log_success "Firewalld topography mapped."
elif command -v ufw &>/dev/null && systemctl is-active --quiet ufw; then
    ufw allow in on waydroid0 >/dev/null 2>&1 || true
    ufw route allow in on waydroid0 >/dev/null 2>&1 || true
    log_success "UFW inbound/routing rules applied."
else
    log_info "No specific firewall manager active. Assuming pure nftables/iptables logic."
fi

# --- 8. ARM Translation (Libhoudini) / casualsnek ---
printf "\n${C_BLUE}--- ARM Translation & Subsystems ---${C_RESET}\n"
read -r -p "Execute casualsnek's waydroid_script (Libhoudini/Magisk)? [y/N] " run_script

if [[ "$run_script" =~ ^[Yy]$ ]]; then
    log_info "Staging Python 3 subsystem..."
    TEMP_DIR=$(mktemp -d)
    
    git clone https://github.com/casualsnek/waydroid_script "$TEMP_DIR"
    
    if ! python3 -m venv "$TEMP_DIR/venv"; then
        log_error "VENV creation failed. Ensure 'python' is installed."
    fi
    
    log_info "Synchronizing dependencies (InquirerPy, tqdm)..."
    "$TEMP_DIR/venv/bin/pip" install -U pip wheel >/dev/null 2>&1
    if ! "$TEMP_DIR/venv/bin/pip" install -r "$TEMP_DIR/requirements.txt" >/dev/null; then
        log_error "Pip dependency fetch failed. Verify network connectivity."
    fi
    
    log_info "Executing python runtime (Root Context)..."
    (cd "$TEMP_DIR" && "$TEMP_DIR/venv/bin/python" main.py)
fi

printf "\n${C_GREEN}===========================================${C_RESET}\n"
printf "${C_GREEN}   Waydroid Topology Complete! ${C_RESET}\n"
printf "${C_GREEN}===========================================${C_RESET}\n"
printf "1. Reboot if core kernel modules were just injected.\n"
printf "2. Launch via user session: waydroid session start\n"
printf "===========================================\n"
