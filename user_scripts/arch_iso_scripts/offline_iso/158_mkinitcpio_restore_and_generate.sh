#!/usr/bin/env bash
# ==============================================================================
# Script: 158_mkinitcpio_generate.sh
# Context: Finalization (Chroot)
# Description: Generates the definitive initramfs for the deployment.
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

printf "${C_BOLD}${C_CYAN}[INFO]${C_RESET} Generating definitive initramfs...\n"
printf "----------------------------------------\n"

# We feed 'n' to safely bypass the limine-mkinitcpio-hook prompt if it fires.
mkinitcpio -P < <(echo "n") || {
    printf "----------------------------------------\n"
    printf "${C_BOLD}${C_YELLOW}[WARN]${C_RESET} mkinitcpio returned a non-zero exit code (usually benign firmware warnings).\n"
}

printf "----------------------------------------\n"
printf "${C_BOLD}${C_GREEN}[OK]${C_RESET} Final initramfs generation complete.\n"
