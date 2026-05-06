#!/usr/bin/env bash
# ==============================================================================
# Script: 125_mkinitcpio_hooks_disable.sh
# Context: Pre-Package Installation (Chroot)
# Description: Safely masks ALPM mkinitcpio hooks via /etc/pacman.d/hooks to 
#              prevent redundant initramfs generation during bulk package installs.
# ==============================================================================
set -euo pipefail

if [[ -t 1 ]]; then
    readonly C_BOLD=$'\033[1m'
    readonly C_CYAN=$'\033[36m'
    readonly C_GREEN=$'\033[32m'
    readonly C_RESET=$'\033[0m'
else
    readonly C_BOLD="" C_CYAN="" C_GREEN="" C_RESET=""
fi

printf "${C_BOLD}${C_CYAN}[INFO]${C_RESET} Masking ALPM mkinitcpio hooks...\n"

# Create the override directory if it doesn't exist
mkdir -p /etc/pacman.d/hooks

# Symlink to /dev/null safely masks the /usr/share/libalpm hooks
ln -sf /dev/null /etc/pacman.d/hooks/90-mkinitcpio-install.hook
ln -sf /dev/null /etc/pacman.d/hooks/60-mkinitcpio-remove.hook

printf "${C_BOLD}${C_GREEN}[OK]${C_RESET} mkinitcpio ALPM hooks successfully disabled.\n"
