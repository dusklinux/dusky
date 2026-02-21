#!/usr/bin/env bash

# ==============================================================================
# GPU Isolation Manager (Arch Linux/Hyprland/UWSM)
# ==============================================================================
#
# Description: Automates binding/unbinding of NVIDIA GPU to VFIO/Host.
#              Surgically edits kernel parameters and regenerates initramfs.
#
# Usage:       ./gpu.sh --bind    (Isolate GPU for VM)
#              ./gpu.sh --unbind  (Return GPU to Host)
#
# ==============================================================================

set -euo pipefail

# --- Configuration Constants ---
readonly GPU_IDS="10de:25a0,10de:2291"
readonly BLACKLIST_MODS="nvidia,nvidia_modeset,nvidia_uvm,nvidia_drm,nouveau"

# Paths
readonly BOOT_CONF="/boot/loader/entries/arch.conf"
readonly MODPROBE_CONF="/etc/modprobe.d/vfio.conf"

# The exact content required for modprobe.d
# Using a standard variable prevents the 'read -d' exit code issue with set -e
readonly VFIO_CONF_CONTENT="options vfio-pci ids=${GPU_IDS}
softdep nvidia pre: vfio-pci
softdep nouveau pre: vfio-pci
blacklist nouveau
blacklist nvidia
blacklist nvidia_drm
blacklist nvidia_modeset"

# --- Styling (ANSI-C Quoting for robustness) ---
readonly BOLD=$'\033[1m'
readonly RED=$'\033[31m'
readonly GREEN=$'\033[32m'
readonly YELLOW=$'\033[33m'
readonly BLUE=$'\033[34m'
readonly RESET=$'\033[0m'

# --- Root Escalation ---
# Uses realpath to ensure the script finds itself if run relatively
if ((EUID != 0)); then
   printf '%s[INFO]%s Script requires root privileges. Elevating...\n' "$YELLOW" "$RESET"
   exec sudo bash "$(realpath "${BASH_SOURCE[0]}")" "$@"
fi

# --- Helper Functions ---

log_info()    { printf '%s[INFO]%s %s\n' "$BLUE" "$RESET" "$1"; }
log_success() { printf '%s[OK]%s %s\n' "$GREEN" "$RESET" "$1"; }
log_err()     { printf '%s[ERROR]%s %s\n' "$RED" "$RESET" "$1" >&2; exit 1; }

validate_boot_conf() {
    [[ -f "$BOOT_CONF" ]] || log_err "Boot entry not found at $BOOT_CONF"
    grep -q '^options[[:space:]]' "$BOOT_CONF" || log_err "No 'options' line found in $BOOT_CONF"
}

# Function to clean VFIO params from kernel config regardless of current state
clean_kernel_params() {
    log_info "Surgically removing existing VFIO/Blacklist parameters from ${BOOT_CONF}..."
    validate_boot_conf
    
    # Single-pass sed for atomicity and efficiency
    # 1. Remove vfio-pci.ids and value
    # 2. Remove module_blacklist and value
    # 3. Normalize whitespace (remove double spaces)
    # 4. Remove trailing whitespace
    sed -i -E \
        -e 's/[[:space:]]+vfio-pci\.ids=[^[:space:]]+//g' \
        -e 's/[[:space:]]+module_blacklist=[^[:space:]]+//g' \
        -e 's/[[:space:]]+/ /g' \
        -e 's/[[:space:]]+$//' \
        "$BOOT_CONF"
}

apply_unbind() {
    log_info "Starting UNBIND process (Switching to Host Mode)..."

    # 1. Remove Modprobe Config
    if [[ -f "$MODPROBE_CONF" ]]; then
        rm -f "$MODPROBE_CONF"
        log_success "Removed $MODPROBE_CONF"
    else
        log_info "$MODPROBE_CONF already absent."
    fi

    # 2. Clean Kernel Params
    clean_kernel_params
    log_success "Kernel parameters sanitized."

    # 3. Regenerate
    log_info "Regenerating initramfs (mkinitcpio)..."
    mkinitcpio -P > /dev/null
    log_success "Initramfs rebuilt."
    
    printf '\n%s%sSUCCESS: GPU Unbound from VFIO.%s\n' "$GREEN" "$BOLD" "$RESET"
    prompt_reboot
}

apply_bind() {
    log_info "Starting BIND process (Switching to VFIO Mode)..."

    # 1. Create Modprobe Config
    printf '%s\n' "$VFIO_CONF_CONTENT" > "$MODPROBE_CONF"
    log_success "Written configuration to $MODPROBE_CONF"

    # 2. Clean first (to prevent duplicates), then Append
    clean_kernel_params
    
    log_info "Injecting VFIO parameters into ${BOOT_CONF}..."
    # Append to the end of the line starting with 'options'
    sed -i "/^options[[:space:]]/ s/$/ vfio-pci.ids=${GPU_IDS} module_blacklist=${BLACKLIST_MODS}/" "$BOOT_CONF"
    log_success "Kernel parameters updated."

    # 3. Regenerate
    log_info "Regenerating initramfs (mkinitcpio)..."
    mkinitcpio -P > /dev/null
    log_success "Initramfs rebuilt."

    printf '\n%s%sSUCCESS: GPU Bound to VFIO.%s\n' "$GREEN" "$BOLD" "$RESET"
    prompt_reboot
}

prompt_reboot() {
    printf '%sA system reboot is required to apply changes.%s\n' "$YELLOW" "$RESET"
    local reply
    # || reply="" prevents failure if user hits Ctrl+D (EOF)
    read -rp "Reboot now? [y/N] " -n 1 reply || reply=""
    echo
    if [[ "${reply,,}" == "y" ]]; then
        log_info "Rebooting..."
        reboot
    else
        log_info "Please reboot manually."
    fi
}

usage() {
    printf '%sUsage:%s %s [OPTIONS]\n' "$BOLD" "$RESET" "$0"
    printf "  --bind    Isolate GPU (VFIO mode)\n"
    printf "  --unbind  Restore GPU (Host/NVIDIA mode)\n"
    exit 1
}

# --- Main Execution ---

if [[ $# -eq 0 ]]; then
    usage
fi

case "$1" in
    --bind)
        apply_bind
        ;;
    --unbind)
        apply_unbind
        ;;
    *)
        log_err "Unknown argument: $1"
        ;;
esac
