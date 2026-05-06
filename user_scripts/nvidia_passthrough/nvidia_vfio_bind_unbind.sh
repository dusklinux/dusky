#!/usr/bin/env bash

# ==============================================================================
# GPU Isolation Manager (Arch Linux/Hyprland/UWSM) - Bootloader Agnostic
# ==============================================================================
#
# Description: Automates binding/unbinding of NVIDIA GPU to VFIO/Host.
#              Relies purely on early-module binding via initramfs.
#              Compatible with Limine, systemd-boot, GRUB, etc.
#
# Usage:       ./gpu.sh --bind    (Isolate GPU for VM)
#              ./gpu.sh --unbind  (Return GPU to Host)
#
# ==============================================================================

set -euo pipefail

# --- Configuration Constants ---
readonly GPU_IDS="10de:25a0,10de:2291"
readonly MODPROBE_CONF="/etc/modprobe.d/vfio.conf"

# The exact content required for modprobe.d
readonly VFIO_CONF_CONTENT="options vfio-pci ids=${GPU_IDS}
softdep nvidia pre: vfio-pci
softdep nouveau pre: vfio-pci
blacklist nouveau
blacklist nvidia
blacklist nvidia_drm
blacklist nvidia_modeset"

# --- Styling ---
readonly BOLD=$'\033[1m'
readonly RED=$'\033[31m'
readonly GREEN=$'\033[32m'
readonly YELLOW=$'\033[33m'
readonly BLUE=$'\033[34m'
readonly RESET=$'\033[0m'

# --- Root Escalation ---
if ((EUID != 0)); then
   printf '%s[INFO]%s Script requires root privileges. Elevating...\n' "$YELLOW" "$RESET"
   exec sudo bash "$(realpath "${BASH_SOURCE[0]}")" "$@"
fi

# --- Helper Functions ---
log_info()    { printf '%s[INFO]%s %s\n' "$BLUE" "$RESET" "$1"; }
log_success() { printf '%s[OK]%s %s\n' "$GREEN" "$RESET" "$1"; }
log_err()     { printf '%s[ERROR]%s %s\n' "$RED" "$RESET" "$1" >&2; exit 1; }

# Ensure modconf is actually in the mkinitcpio hooks
verify_mkinitcpio_hooks() {
    if ! grep -q "^HOOKS=.*modconf" /etc/mkinitcpio.conf; then
        log_err "'modconf' hook missing in /etc/mkinitcpio.conf. Required for early VFIO binding."
    fi
}

# Safely rebuilds initramfs and forces Limine to resync
rebuild_initramfs() {
    log_info "Regenerating initramfs and updating bootloader entries..."
    verify_mkinitcpio_hooks
    
    # Temporarily disable pipefail so 'yes' receiving SIGPIPE doesn't kill the script
    set +o pipefail
    yes | mkinitcpio -P > /dev/null
    set -o pipefail

    # The pipe above causes the Limine pacman hook to skip its interactive prompt.
    # We explicitly call limine-update here to guarantee the hashes stay synced.
    if command -v limine-update >/dev/null 2>&1; then
        limine-update > /dev/null
        log_success "Limine boot configuration synchronized."
    fi
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

    # 2. Regenerate
    rebuild_initramfs
    log_success "Initramfs rebuilt. VFIO modules purged from boot image."
    
    printf '\n%s%sSUCCESS: GPU Unbound from VFIO.%s\n' "$GREEN" "$BOLD" "$RESET"
    prompt_reboot
}

apply_bind() {
    log_info "Starting BIND process (Switching to VFIO Mode)..."

    # 1. Create Modprobe Config
    printf '%s\n' "$VFIO_CONF_CONTENT" > "$MODPROBE_CONF"
    log_success "Written VFIO early-bind configuration to $MODPROBE_CONF"

    # 2. Regenerate
    rebuild_initramfs
    log_success "Initramfs rebuilt. VFIO modules injected into boot image."

    printf '\n%s%sSUCCESS: GPU Bound to VFIO.%s\n' "$GREEN" "$BOLD" "$RESET"
    prompt_reboot
}

prompt_reboot() {
    printf '%sA system reboot is required to apply changes.%s\n' "$YELLOW" "$RESET"
    local reply
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
