#!/usr/bin/env bash
# ==============================================================================
# Script: 154_mkinitcpio_hooks_restore.sh
# Context: Post-Package Installation (Chroot)
# Description: Unmasks ALPM hooks to allow limine-mkinitcpio-hook to install cleanly.
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

printf "${C_BOLD}${C_CYAN}[INFO]${C_RESET} Unmasking pacman mkinitcpio hooks...\n"

# Remove the overrides to restore normal system behavior and clear the path 
# for limine-mkinitcpio-hook to lay down its own tracking files.
rm -f /etc/pacman.d/hooks/90-mkinitcpio-install.hook
rm -f /etc/pacman.d/hooks/60-mkinitcpio-remove.hook

printf "${C_BOLD}${C_GREEN}[OK]${C_RESET} mkinitcpio ALPM hooks successfully restored.\n"
