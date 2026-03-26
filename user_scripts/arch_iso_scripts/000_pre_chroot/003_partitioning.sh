#!/usr/bin/env bash
# ==============================================================================
# MODULE: 003_partitioning.sh
# CONTEXT: Arch ISO Environment
# PURPOSE: Block Device Prep, GPT, LUKS2 Encryption, Base Filesystem Creation
# ==============================================================================

set -euo pipefail

# Visual Constants
readonly C_BOLD=$'\033[1m'
readonly C_RED=$'\033[31m'
readonly C_GREEN=$'\033[32m'
readonly C_YELLOW=$'\033[33m'
readonly C_CYAN=$'\033[36m'
readonly C_RESET=$'\033[0m'

# --- Signal Handling & Cleanup ---
cleanup() {
    local status=${1:-0}
    # Unset traps immediately to prevent recursive firing on exit
    trap - EXIT INT TERM
    
    # Restore terminal cursor if hidden during password prompts
    tput cnorm 2>/dev/null || true
    printf '%b\n' "$C_RESET"
    exit "$status"
}

trap 'cleanup "$?"' EXIT
trap 'cleanup 130' INT
trap 'cleanup 143' TERM

# --- Boot Mode Detection ---
if [[ -d /sys/firmware/efi/efivars ]]; then
    readonly BOOT_MODE="UEFI"
else
    readonly BOOT_MODE="BIOS"
fi

# --- Helper: Partition Naming ---
# Correctly maps /dev/nvme0n1 -> /dev/nvme0n1p1 and /dev/sda -> /dev/sda1
get_partition_path() {
    local dev="$1"
    local num="$2"
    if [[ "$dev" =~ (nvme|mmcblk|loop) ]]; then
        echo "${dev}p${num}"
    else
        echo "${dev}${num}"
    fi
}

# --- Shared: Secure LUKS Prompt ---
# Guarantees whitespace preservation (IFS=) and standard out isolation (>&2)
prompt_luks_password() {
    local pass1 pass2
    while true; do
        printf 'Enter new LUKS2 passphrase for Root: ' >&2
        IFS= read -r -s pass1
        printf '\n' >&2

        printf 'Verify LUKS2 passphrase: ' >&2
        IFS= read -r -s pass2
        printf '\n' >&2

        if [[ -n "$pass1" && "$pass1" == "$pass2" ]]; then
            printf '%s' "$pass1"
            return 0
        fi

        printf '%b\n\n' "${C_RED}Passphrases empty or do not match. Try again.${C_RESET}" >&2
    done
}

# --- Autonomous Execution Flow ---
run_auto_mode() {
    clear
    echo -e "${C_BOLD}=== AUTONOMOUS DISK PROVISIONING (${C_CYAN}${BOOT_MODE}${C_RESET}${C_BOLD}) ===${C_RESET}\n"

    lsblk -d -e 7,11 -o NAME,SIZE,MODEL,TYPE,RO | grep -v "loop"
    echo ""

    read -r -p "Enter target drive to WIPE and PROVISION (e.g., nvme0n1): " raw_drive
    local target_dev="/dev/${raw_drive#/dev/}"

    # Fail-fast block device validation
    if [[ ! -b "$target_dev" ]]; then
        echo -e "${C_RED}Critical: Block device $target_dev not found. Aborting.${C_RESET}"
        exit 1
    fi

    local luks_pass
    luks_pass=$(prompt_luks_password)

    echo -e "\n${C_RED}${C_BOLD}!!! WARNING: WIPING ALL DATA ON $target_dev IN 5 SECONDS !!!${C_RESET}"
    sleep 5

    echo -e "${C_YELLOW}>> Zapping partition table...${C_RESET}"
    wipefs -a "$target_dev"
    sgdisk --zap-all "$target_dev"

    echo -e "${C_YELLOW}>> Writing new GPT layout...${C_RESET}"
    if [[ "$BOOT_MODE" == "UEFI" ]]; then
        sgdisk -n 1:0:+2G -t 1:ef00 -c 1:"EFI System" "$target_dev"
        sgdisk -n 2:0:0   -t 2:8309 -c 2:"Linux LUKS" "$target_dev"
    else
        sgdisk -n 1:0:+1M -t 1:ef02 -c 1:"BIOS Boot"  "$target_dev"
        sgdisk -n 2:0:0   -t 2:8309 -c 2:"Linux LUKS" "$target_dev"
    fi

    # Critical: Wait for kernel to map the new block devices before continuing
    partprobe "$target_dev"
    udevadm settle

    local part_boot
    local part_root
    part_boot=$(get_partition_path "$target_dev" 1)
    part_root=$(get_partition_path "$target_dev" 2)

    echo -e "${C_YELLOW}>> Encrypting Root Partition ($part_root)...${C_RESET}"
    printf '%s' "$luks_pass" | cryptsetup -q luksFormat --type luks2 --key-file - "$part_root"
    
    # Modern SSD/NVMe optimization: --allow-discards enables BTRFS TRIM pass-through
    printf '%s' "$luks_pass" | cryptsetup open --allow-discards --key-file - "$part_root" cryptroot

    echo -e "${C_YELLOW}>> Formatting Filesystems...${C_RESET}"
    mkfs.btrfs -f -L "ARCH_ROOT" /dev/mapper/cryptroot

    if [[ "$BOOT_MODE" == "UEFI" ]]; then
        mkfs.fat -F 32 -n "EFI" "$part_boot"
    fi

    echo -e "${C_GREEN}>> Autonomous Provisioning Complete.${C_RESET}"
}

# --- Interactive Execution Flow ---
run_interactive_mode() {
    clear
    echo -e "${C_BOLD}=== INTERACTIVE DISK PROVISIONING (${C_CYAN}${BOOT_MODE}${C_RESET}${C_BOLD}) ===${C_RESET}\n"

    lsblk -d -e 7,11 -o NAME,SIZE,MODEL,TYPE,RO | grep -v "loop"
    echo ""
    read -r -p "Enter drive to partition via cfdisk (e.g., nvme0n1): " raw_drive
    local target_dev="/dev/${raw_drive#/dev/}"

    if [[ ! -b "$target_dev" ]]; then
        echo -e "${C_RED}Error: Device not found. Aborting.${C_RESET}"
        exit 1
    fi

    # Bypassing orchestrator pipes to allow ncurses UI
    cfdisk "$target_dev" < /dev/tty > /dev/tty 2>&1
    partprobe "$target_dev"
    udevadm settle

    echo -e "\n${C_GREEN}>> Partitioning finished. Please specify the new layout.${C_RESET}"
    lsblk -o NAME,SIZE,TYPE,FSTYPE "$target_dev"

    read -r -p "Enter the new ROOT partition (e.g., nvme0n1p2): " raw_root
    local part_root="/dev/${raw_root#/dev/}"
    
    # Immediate Fail-Fast Validation
    if [[ ! -b "$part_root" ]]; then
        echo -e "${C_RED}Critical: Root partition $part_root not found. Aborting.${C_RESET}"
        exit 1
    fi

    local part_efi=""
    if [[ "$BOOT_MODE" == "UEFI" ]]; then
        read -r -p "Enter the new EFI partition (e.g., nvme0n1p1): " raw_efi
        part_efi="/dev/${raw_efi#/dev/}"

        # Immediate Fail-Fast Validation
        if [[ ! -b "$part_efi" ]]; then
            echo -e "${C_RED}Critical: EFI partition $part_efi not found. Aborting.${C_RESET}"
            exit 1
        fi
    fi

    local luks_pass
    luks_pass=$(prompt_luks_password)

    echo -e "${C_YELLOW}>> Encrypting Root Partition ($part_root)...${C_RESET}"
    printf '%s' "$luks_pass" | cryptsetup -q luksFormat --type luks2 --key-file - "$part_root"
    
    # Modern SSD/NVMe optimization: --allow-discards enables BTRFS TRIM pass-through
    printf '%s' "$luks_pass" | cryptsetup open --allow-discards --key-file - "$part_root" cryptroot

    echo -e "${C_YELLOW}>> Formatting Root (BTRFS)...${C_RESET}"
    mkfs.btrfs -f -L "ARCH_ROOT" /dev/mapper/cryptroot

    if [[ "$BOOT_MODE" == "UEFI" ]]; then
        echo -e "${C_YELLOW}>> Formatting EFI (FAT32)...${C_RESET}"
        mkfs.fat -F 32 -n "EFI" "$part_efi"
    fi

    echo -e "${C_GREEN}>> Interactive Provisioning Complete.${C_RESET}"
}

# --- Entry Logic ---
if [[ "${1:-}" == "--auto" || "${1:-}" == "auto" ]]; then
    run_auto_mode
else
    read -r -p "Run AUTONOMOUS wipe and provision? [y/N]: " choice
    if [[ "${choice,,}" == "y" ]]; then
        run_auto_mode
    else
        run_interactive_mode
    fi
fi

exit 0
