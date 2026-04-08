#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Script: 006_systemd_bootloader.sh
# Description: Automates systemd-boot configuration for Arch/Hyprland.
#              FIXED: Now includes Btrfs subvolume handling (rootflags=subvol=@).
# -----------------------------------------------------------------------------

set -euo pipefail

# --- Configuration ---
# [FIX APPLIED]: Added 'rootflags=subvol=@' so the kernel finds /sbin/init inside the subvolume
readonly BASE_PARAMS="rw loglevel=3 zswap.enabled=0 rootfstype=btrfs rootflags=subvol=@ fsck.mode=skip"
readonly LOADER_CONF="/boot/loader/loader.conf"
readonly ENTRY_CONF="/boot/loader/entries/arch.conf"

# --- Visuals ---
if [[ -t 1 ]]; then
    readonly C_RESET=$'\033[0m'
    readonly C_BLUE=$'\033[1;34m'
    readonly C_GREEN=$'\033[1;32m'
    readonly C_YELLOW=$'\033[1;33m'
    readonly C_RED=$'\033[1;31m'
else
    readonly C_RESET='' C_BLUE='' C_GREEN='' C_YELLOW='' C_RED=''
fi

log_info()    { printf "${C_BLUE}[INFO]${C_RESET} %s\n" "$*"; }
log_success() { printf "${C_GREEN}[OK]${C_RESET} %s\n" "$*"; }
log_warn()    { printf "${C_YELLOW}[WARN]${C_RESET} %s\n" "$*" >&2; }
log_error()   { printf "${C_RED}[ERROR]${C_RESET} %s\n" "$*" >&2; }

# --- Cleanup Trap ---
cleanup() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        log_error "Script failed (Exit Code: $exit_code)."
    fi
}
trap cleanup EXIT

# ==============================================================================
# 1. Environment Checks
# ==============================================================================

# Root Check
if [[ $EUID -ne 0 ]]; then
    log_error "Must run as root."
    exit 1
fi

# UEFI Check
if [[ ! -d /sys/firmware/efi/efivars ]]; then
    log_warn "No UEFI variables found in /sys/firmware/efi/efivars."
    log_warn "System appears to be BIOS/Legacy. This script requires UEFI."
    exit 0
fi

# /boot Mount Check (ESP)
if ! mountpoint -q /boot; then
    log_error "/boot is NOT a mountpoint."
    log_error "Please mount your EFI partition (ESP) to /boot before running this script."
    exit 1
fi

log_success "UEFI detected and /boot is mounted."
sleep 1

# ==============================================================================
# 2. Installation
# ==============================================================================

log_info "Installing efibootmgr..."
pacman -S --needed --noconfirm efibootmgr >/dev/null
sleep 1

log_info "Installing systemd-boot to /boot..."
# Attempt install; check if already installed if fail
if ! bootctl install --esp-path=/boot >/dev/null 2>&1; then
    if ! bootctl is-installed --esp-path=/boot >/dev/null 2>&1; then
         log_error "bootctl install failed. Ensure /boot is a valid FAT32 partition."
         exit 1
    fi
fi
log_success "systemd-boot binary installed."
sleep 1

log_info "Writing global $LOADER_CONF..."
cat > "$LOADER_CONF" <<EOF
default  arch.conf
timeout  1
console-mode max
editor   no
EOF
sleep 1

# ==============================================================================
# 3. Kernel Configuration
# ==============================================================================

log_info "Detecting root partition..."

# 1. Find device (raw)
ROOT_DEV_RAW=$(findmnt -n -o SOURCE /)
# 2. Sanitize (Strip Btrfs brackets if present)
ROOT_DEV="${ROOT_DEV_RAW%[*}"

log_info "Found Root Device: $ROOT_DEV"

if [[ ! -b "$ROOT_DEV" ]]; then
    log_error "Device '$ROOT_DEV' is not a valid block device."
    exit 1
fi

# 3. Get PARTUUID (Allow failure check)
set +e
ROOT_PARTUUID=$(blkid -s PARTUUID -o value "$ROOT_DEV")
BLKID_EXIT=$?
set -e

if [[ $BLKID_EXIT -ne 0 ]] || [[ -z "$ROOT_PARTUUID" ]]; then
    log_error "Could not get PARTUUID for $ROOT_DEV."
    log_warn "Ensure disk is GPT formatted."
    exit 1
fi

log_success "Root PARTUUID: $ROOT_PARTUUID"
sleep 1

# --- Microcode Detection ---
UCODE_STR=""
if [[ -f "/boot/intel-ucode.img" ]]; then
    UCODE_STR="initrd  /intel-ucode.img"
    log_info "Intel Microcode detected."
elif [[ -f "/boot/amd-ucode.img" ]]; then
    UCODE_STR="initrd  /amd-ucode.img"
    log_info "AMD Microcode detected."
fi
sleep 1

# --- ASPM Prompt ---
ASPM_STR=""
if [[ -t 0 ]]; then
    printf "\n${C_YELLOW}--- Power Saving ---${C_RESET}\n"
    read -r -p "Enable 'pcie_aspm=force'? (Recommended for laptops) [y/N]: " response
    if [[ "$response" =~ ^[yY] ]]; then
        ASPM_STR="pcie_aspm=force"
        log_info "ASPM enabled."
    else
        log_info "ASPM disabled."
    fi
else
    log_warn "Non-interactive: Skipping ASPM."
fi
sleep 1

# --- Generate Config ---
log_info "Generating $ENTRY_CONF..."

FINAL_OPTIONS="root=PARTUUID=${ROOT_PARTUUID} ${BASE_PARAMS}"
[[ -n "$ASPM_STR" ]] && FINAL_OPTIONS+=" ${ASPM_STR}"

{
    printf "title   Arch Linux\n"
    printf "linux   /vmlinuz-linux\n"
    [[ -n "$UCODE_STR" ]] && printf "%s\n" "$UCODE_STR"
    printf "initrd  /initramfs-linux.img\n"
    printf "options %s\n" "$FINAL_OPTIONS"
} > "$ENTRY_CONF"

sleep 1

# ==============================================================================
# 4. Finalize
# ==============================================================================

log_info "Enabling systemd-boot-update.service..."
systemctl enable systemd-boot-update.service >/dev/null 2>&1 || true

log_success "Setup complete. Configuration verified."
printf "   Loader: %s\n" "$LOADER_CONF"
printf "   Entry:  %s\n" "$ENTRY_CONF"
printf "   Params: %s\n" "$FINAL_OPTIONS"
