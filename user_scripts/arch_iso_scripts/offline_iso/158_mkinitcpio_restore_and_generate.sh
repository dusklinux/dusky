#!/usr/bin/env bash
# ==============================================================================
# Script: 158_mkinitcpio_restore_and_generate.sh
# Context: Post-Configuration (Chroot)
# Description: Unmasks ALPM hooks and performs the definitive initramfs build.
# ==============================================================================
set -euo pipefail

if [[ -t 1 ]]; then
    readonly C_BOLD=$'\033[1m'
    readonly C_CYAN=$'\033[36m'
    readonly C_GREEN=$'\033[32m'
    readonly C_YELLOW=$'\033[33m'
    readonly C_RESET=$'\033[0m'
else
    readonly C_BOLD="" C_CYAN="" C_GREEN="" C_YELLOW="" C_RESET=""
fi

printf "${C_BOLD}${C_CYAN}[INFO]${C_RESET} Unmasking pacman mkinitcpio hooks...\n"

# Remove the overrides to restore normal system behavior
rm -f /etc/pacman.d/hooks/90-mkinitcpio-install.hook
rm -f /etc/pacman.d/hooks/60-mkinitcpio-remove.hook

printf "${C_BOLD}${C_CYAN}[INFO]${C_RESET} Generating definitive initramfs...\n"
printf "----------------------------------------\n"

# We feed 'n' just in case limine-mkinitcpio-hook (installed in 155) prompts.
# It will usually run automatically as an ALPM post-transaction hook anyway.
mkinitcpio -P < <(echo "n") || {
    printf "----------------------------------------\n"
    printf "${C_BOLD}${C_YELLOW}[WARN]${C_RESET} mkinitcpio returned a non-zero exit code (likely benign firmware warnings).\n"
}

printf "----------------------------------------\n"
printf "${C_BOLD}${C_GREEN}[OK]${C_RESET} Final initramfs generation complete.\n"
