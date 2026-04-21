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

tmp_fstab=""
cleanup() {
    if [[ -n "${tmp_fstab:-}" && -e "${tmp_fstab}" ]]; then
        rm -f -- "${tmp_fstab}"
    fi
}
trap cleanup EXIT

# 1. Pre-Flight Checks
if ! command -v genfstab >/dev/null 2>&1; then
    echo -e "${C_RED}ERROR:${C_RESET} 'genfstab' is not installed. Please install arch-install-scripts."
    exit 1
fi

if ! mountpoint -q /mnt; then
    echo -e "${C_RED}ERROR:${C_RESET} Nothing is mounted at /mnt. Please mount your partitions first."
    exit 1
fi

# 2. Determine Execution Mode (--auto flag or AUTO_MODE=1 env var)
IS_AUTO="${AUTO_MODE:-0}"
for arg in "$@"; do
    if [[ "$arg" == "--auto" ]]; then
        IS_AUTO=1
        break
    fi
done

if [[ "${IS_AUTO}" == "1" ]]; then
    echo -e "\n${C_CYAN}>> Auto-mode detected:${C_RESET} Bypassing prompt and automatically generating fstab."
    response="y"
else
    # Interactive mode
    echo -e "\n${C_YELLOW}WARNING:${C_RESET} Regenerating fstab will overwrite your existing file."
    read -r -p "Do you want to generate a new fstab? [Y/n] " response
fi

# 3. Execution
if [[ "${response,,}" =~ ^(y|yes|)$ ]]; then
    echo ">> Generating Fstab..."

    # Ensure target directory exists
    mkdir -p /mnt/etc

    # Backup existing fstab if it exists and is not empty
    if [[ -s /mnt/etc/fstab ]]; then
        local_backup="/mnt/etc/fstab.bak.$(date +%F_%H-%M-%S)"
        cp /mnt/etc/fstab "$local_backup"
        echo "   -> Existing fstab backed up to: $local_backup"
    fi

    # Generate into a temporary file first to avoid truncating the real fstab
    # unless generation and post-processing both succeed.
    tmp_fstab=$(mktemp /mnt/etc/fstab.tmp.XXXXXX)

    # Generate the initial Fstab
    genfstab -U /mnt > "$tmp_fstab"

    if [[ ! -s "$tmp_fstab" ]]; then
        echo -e "${C_RED}ERROR:${C_RESET} genfstab produced an empty file. Aborting to avoid overwriting /mnt/etc/fstab."
        exit 1
    fi

    # CRITICAL BTRFS FIX: Strip subvolid cleanly without leaving floating commas
    echo ">> Stripping hardcoded subvolid parameters for Btrfs snapshot compatibility..."
    # Pass 1: Removes subvolid and its preceding comma
    # Pass 2: Removes subvolid and its trailing comma (if it was the first option)
    # Pass 3: Removes subvolid if it somehow ended up alone
    sed -i -E 's/,subvolid=[0-9]+//g; s/subvolid=[0-9]+,//g; s/subvolid=[0-9]+//g' "$tmp_fstab"

    # Match existing permissions if possible; otherwise use conventional fstab perms
    if [[ -e /mnt/etc/fstab ]]; then
        chmod --reference=/mnt/etc/fstab "$tmp_fstab"
    else
        chmod 644 "$tmp_fstab"
    fi

    mv -f -- "$tmp_fstab" /mnt/etc/fstab
    tmp_fstab=""

    # Verify & Print
    echo -e "\n${C_GREEN}=== /mnt/etc/fstab contents ===${C_RESET}"
    cat /mnt/etc/fstab

    echo -e "\n[${C_GREEN}SUCCESS${C_RESET}] Fstab generated and optimized."

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
