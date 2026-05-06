#!/bin/bash
# -------------------------------------------------------------------------
# Arch Linux / Hyprland / UWSM Ecosystem - Intelligent GRUB Installer
# -------------------------------------------------------------------------
# AUTHOR: Elite DevOps Engineer
# CONTEXT: Must be run inside 'arch-chroot /mnt'
# FEATURES: 
#   - Auto-detects BIOS vs UEFI
#   - Checks for existing systemd-boot installation
#   - Auto-resolves parent drive for BIOS MBR installation
#   - Bash 5.0+ optimizations
# -------------------------------------------------------------------------

set -euo pipefail

# --- Visual formatting constants ---
readonly GREEN=$'\033[0;32m'
readonly YELLOW=$'\033[1;33m'
readonly RED=$'\033[0;31m'
readonly CYAN=$'\033[0;36m'
readonly NC=$'\033[0m' # No Color

# --- Helper Functions ---

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
    sleep 1
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
    sleep 1
}

log_crit() {
    echo -e "${RED}[CRITICAL]${NC} $1"
    sleep 1
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_crit "This script must be run as root (inside arch-chroot)."
        exit 1
    fi
}

# --- Main Logic ---

check_systemd_boot() {
    log_info "Checking for existing Bootloaders..."
    
    # Check for systemd-boot configuration file
    if [[ -f "/boot/loader/loader.conf" ]]; then
        echo -e "${GREEN}-----------------------------------------------------${NC}"
        echo -e "${GREEN}SUCCESS: systemd-boot is already configured on this system.${NC}"
        echo -e "${GREEN}Skipping GRUB installation to avoid conflicts.${NC}"
        echo -e "${GREEN}-----------------------------------------------------${NC}"
        sleep 1
        exit 0
    fi
    
    # Secondary check using bootctl if available
    if command -v bootctl &> /dev/null; then
        if bootctl is-installed &> /dev/null; then
            echo -e "${GREEN}SUCCESS: systemd-boot binary detected.${NC}" 
            exit 0
        fi
    fi

    log_info "No systemd-boot configuration found. Proceeding with GRUB..."
}

identify_firmware_mode() {
    log_info "Identifying Mainboard Firmware Mode..."
    
    if [[ -d "/sys/firmware/efi/efivars" ]]; then
        echo -e "${CYAN}:: UEFI Mode Detected.${NC}"
        sleep 1
        install_grub_uefi
    else
        echo -e "${CYAN}:: BIOS/Legacy Mode Detected.${NC}"
        sleep 1
        install_grub_bios
    fi
}

install_grub_uefi() {
    echo -e "${YELLOW}-----------------------------------------------------${NC}"
    echo -e "${YELLOW}RECOMMENDATION: You are on a UEFI system.${NC}"
    echo -e "${YELLOW}systemd-boot is faster and simpler for Arch Linux.${NC}"
    echo -e "${YELLOW}-----------------------------------------------------${NC}"
    
    read -r -p "Do you still wish to proceed with GRUB? [y/N] " response
    if [[ ! "${response,,}" =~ ^y ]]; then
        log_info "Aborting GRUB installation per user request."
        exit 0
    fi

    log_info "Installing GRUB packages for UEFI..."
    pacman -S --noconfirm grub efibootmgr
    
    log_info "Installing GRUB bootloader to EFI partition..."
    # Assumes /boot is the mount point for ESP (Standard Arch practice)
    grub-install --target=x86_64-efi --efi-directory=/boot --bootloader-id=GRUB
    
    generate_config
}

install_grub_bios() {
    log_info "Installing GRUB packages for BIOS..."
    pacman -S --noconfirm grub

    # Bash 5+ Logic to determine the parent disk of the root partition
    # This prevents installing to a partition (e.g., sda1) which fails on BIOS
    log_info "Detecting installation target drive..."
    
    # Get the device mounted at /
    local root_dev
    root_dev=$(findmnt / -o SOURCE -n)

    # Sanity check
    if [[ -z "$root_dev" ]]; then
        log_crit "Could not determine root device. Install GRUB manually."
        exit 1
    fi

    # Regex to strip partition numbers (handles /dev/sda1 -> /dev/sda AND /dev/nvme0n1p1 -> /dev/nvme0n1)
    local target_disk
    if [[ "$root_dev" =~ "nvme" || "$root_dev" =~ "mmcblk" ]]; then
        target_disk="${root_dev%p*}"
    else
        target_disk="${root_dev%%[0-9]*}"
    fi

    echo -e "Detected Root: ${CYAN}$root_dev${NC}"
    echo -e "Target Disk: ${CYAN}$target_disk${NC}"
    sleep 1

    log_info "Installing GRUB MBR to $target_disk..."
    grub-install --target=i386-pc "$target_disk"
    
    generate_config
}

generate_config() {
    log_info "Generating GRUB configuration file..."
    
    # Check if os-prober is needed (for dual boot users)
    # Only useful if os-prober is installed and meant to be used
    if command -v os-prober &> /dev/null; then
        log_info "os-prober detected. Ensuring it is enabled in GRUB..."
        # Quick sed patch to enable os-prober if disabled by default in newer GRUB
        sed -i 's/#GRUB_DISABLE_OS_PROBER=false/GRUB_DISABLE_OS_PROBER=false/' /etc/default/grub
    fi

    grub-mkconfig -o /boot/grub/grub.cfg
    
    if [[ $? -eq 0 ]]; then
        echo -e "${GREEN}-----------------------------------------------------${NC}"
        echo -e "${GREEN}SUCCESS: GRUB has been successfully installed and configured.${NC}"
        echo -e "${GREEN}-----------------------------------------------------${NC}"
    else
        log_crit "GRUB configuration generation failed."
        exit 1
    fi
    sleep 1
}

# --- Execution Flow ---
check_root
check_systemd_boot
identify_firmware_mode

exit 0
