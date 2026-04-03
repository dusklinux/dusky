#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# MODULE: FSTAB GENERATION
# -----------------------------------------------------------------------------
set -euo pipefail

readonly C_GREEN=$'\033[32m'
readonly C_YELLOW=$'\033[33m'
readonly C_RED=$'\033[31m'
readonly C_CYAN=$'\033[36m'
readonly C_RESET=$'\033[0m'

# 1. Ask for confirmation with a warning
echo -e "\n${C_YELLOW}WARNING:${C_RESET} If you are mounting an existing system to repair it (arch-chroot),"
echo "regenerating fstab will overwrite your existing file and discard manual entries."
read -r -p "Do you want to generate a new fstab? [Y/n] " response

# 2. Conditional Execution
if [[ "${response,,}" =~ ^(y|yes|)$ ]]; then
    echo ">> Generating Fstab..."

    # Ensure target directory exists (safety fallback)
    mkdir -p /mnt/etc

    # Generate the initial Fstab
    genfstab -U /mnt > /mnt/etc/fstab

    # CRITICAL BTRFS FIX: Strip subvolid to allow for snapshot rollbacks
    echo ">> Stripping hardcoded subvolid parameters for Btrfs snapshot compatibility..."
    sed -i -E 's/(^|,)subvolid=[0-9]+//g' /mnt/etc/fstab

    # Verify & Print
    echo -e "\n${C_GREEN}=== /mnt/etc/fstab contents ===${C_RESET}"
    cat /mnt/etc/fstab

    echo -e "\n[${C_GREEN}SUCCESS${C_RESET}] Fstab generated and optimized for Btrfs."

    # CRITICAL NEXT STEP PROMPT
    echo -e "\n${C_RED}##########################################################${C_RESET}"
    echo -e "${C_RED}##             CRITICAL NEXT STEP REQUIRED              ##${C_RESET}"
    echo -e "${C_RED}##########################################################${C_RESET}"
    echo -e "${C_YELLOW}You must now enter the new system environment manually.${C_RESET}"
    echo -e "${C_YELLOW}Please type the following command exactly:${C_RESET}\n"
    echo -e "    ${C_CYAN}arch-chroot /mnt${C_RESET}\n"
    echo -e "${C_RED}##########################################################${C_RESET}\n"

else
    echo ">> Skipping fstab generation as requested."
fi
