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

readonly TARGET_CRYPT_NAME="cryptroot"
OPENED_CRYPTROOT=0

# --- Signal Handling & Cleanup ---
cleanup() {
    local status=${1:-0}

    # Prevent recursive trap firing
    trap - EXIT INT TERM

    # If this run opened cryptroot but failed later, close it.
    # On success, keep it open for the next module (004_disk_mount.sh).
    if (( status != 0 )) && (( OPENED_CRYPTROOT == 1 )) && [[ -b "/dev/mapper/${TARGET_CRYPT_NAME}" ]]; then
        cryptsetup close "${TARGET_CRYPT_NAME}" 2>/dev/null || true
        udevadm settle 2>/dev/null || true
    fi

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
get_partition_path() {
    local dev_path="$1"
    local num="$2"
    local dev_name="${dev_path##*/}"

    if [[ "$dev_name" =~ ^(nvme|mmcblk|loop) ]]; then
        printf '%s\n' "${dev_path}p${num}"
    else
        printf '%s\n' "${dev_path}${num}"
    fi
}

# --- Helper: Wait for Block Device Node ---
wait_for_block_device() {
    local dev="$1"
    local timeout="${2:-10}"
    local i

    for (( i=0; i<timeout*10; i++ )); do
        [[ -b "$dev" ]] && return 0
        sleep 0.1
    done

    return 1
}

# --- Helper: Is Node Backed by Target Disk? ---
device_is_on_disk() {
    local node
    local disk
    local parent

    node=$(readlink -f "$1")
    disk=$(readlink -f "$2")

    [[ -b "$node" && -b "$disk" ]] || return 1

    while true; do
        [[ "$node" == "$disk" ]] && return 0

        parent=$(lsblk -ndo PKNAME "$node" 2>/dev/null | head -n1 || true)
        [[ -n "$parent" ]] || return 1

        node="/dev/${parent}"
    done
}

# --- Helper: Validate Target Disk ---
validate_target_disk() {
    local dev="$1"
    local dev_type
    local ro
    local boot_src

    if [[ ! -b "$dev" ]]; then
        echo -e "${C_RED}Critical: Block device $dev not found. Aborting.${C_RESET}"
        exit 1
    fi

    dev_type=$(lsblk -ndo TYPE "$dev" 2>/dev/null | head -n1 || true)
    ro=$(lsblk -ndo RO "$dev" 2>/dev/null | head -n1 || true)

    if [[ "$dev_type" != "disk" ]]; then
        echo -e "${C_RED}Critical: $dev is not a whole disk. Aborting.${C_RESET}"
        exit 1
    fi

    if [[ "$ro" != "0" ]]; then
        echo -e "${C_RED}Critical: $dev is read-only. Aborting.${C_RESET}"
        exit 1
    fi

    # Protect the live Arch ISO boot media when booted from USB storage
    boot_src=$(findmnt -rn -o SOURCE /run/archiso/bootmnt 2>/dev/null || true)
    if [[ -n "$boot_src" && -b "$boot_src" ]] && device_is_on_disk "$boot_src" "$dev"; then
        echo -e "${C_RED}Critical: $dev appears to host the live Arch ISO boot media. Refusing to wipe it.${C_RESET}"
        exit 1
    fi
}

# --- Helper: Validate Chosen Partition ---
validate_partition_on_target() {
    local part="$1"
    local target_dev="$2"
    local label="$3"
    local part_type

    if [[ ! -b "$part" ]]; then
        echo -e "${C_RED}Critical: ${label} partition $part not found. Aborting.${C_RESET}"
        exit 1
    fi

    part_type=$(lsblk -ndo TYPE "$part" 2>/dev/null | head -n1 || true)
    if [[ "$part_type" != "part" ]]; then
        echo -e "${C_RED}Critical: ${label} device $part is not a partition. Aborting.${C_RESET}"
        exit 1
    fi

    if ! device_is_on_disk "$part" "$target_dev"; then
        echo -e "${C_RED}Critical: ${label} partition $part does not belong to $target_dev. Aborting.${C_RESET}"
        exit 1
    fi
}

# --- Helper: Ensure Reserved Mapper Name is Safe ---
ensure_mapper_name_available() {
    local target_dev="$1"
    local backing=""

    if [[ -b "/dev/mapper/${TARGET_CRYPT_NAME}" ]]; then
        backing=$(cryptsetup status "${TARGET_CRYPT_NAME}" 2>/dev/null | awk -F': *' '$1 ~ /^[[:space:]]*device$/ {print $2; exit}' || true)

        if [[ -n "$backing" && -b "$backing" ]] && device_is_on_disk "$backing" "$target_dev"; then
            echo -e "${C_YELLOW}>> Releasing existing ${TARGET_CRYPT_NAME} mapper on $target_dev...${C_RESET}"
            cryptsetup close "${TARGET_CRYPT_NAME}" 2>/dev/null || true
            udevadm settle
        else
            echo -e "${C_RED}Critical: /dev/mapper/${TARGET_CRYPT_NAME} already exists and does not belong to $target_dev. Aborting to avoid collateral damage.${C_RESET}"
            exit 1
        fi
    fi
}

# --- Helper: Teardown Active Disk Locks ---
teardown_device() {
    local dev="$1"
    local swap_name
    local swap_src
    local node
    local src
    local mp
    local i

    local -A mount_targets=()
    local -a crypts=()

    # 1. Disable active swap backed by this device tree
    while IFS= read -r swap_name; do
        [[ -n "$swap_name" ]] || continue

        if [[ -b "$swap_name" ]]; then
            swap_src="$swap_name"
        else
            swap_src=$(findmnt -rn -o SOURCE --target "$swap_name" 2>/dev/null | head -n1 || true)
        fi

        if [[ -n "$swap_src" && -b "$swap_src" ]] && device_is_on_disk "$swap_src" "$dev"; then
            echo -e "${C_YELLOW}>> Disabling active swap on $dev...${C_RESET}"
            swapoff "$swap_name" 2>/dev/null || true
        fi
    done < <(swapon --show=NAME --noheadings 2>/dev/null || true)

    # 2. Unmount all mountpoints backed by this device tree
    while IFS= read -r node; do
        [[ -b "$node" ]] || continue

        for src in "$node" "$(readlink -f "$node")"; do
            [[ -n "$src" ]] || continue
            while IFS= read -r mp; do
                [[ -n "$mp" ]] || continue
                mount_targets["$mp"]=1
            done < <(findmnt -rn -S "$src" -o TARGET 2>/dev/null || true)
        done
    done < <(lsblk -pnro NAME "$dev" 2>/dev/null || true)

    if (( ${#mount_targets[@]} > 0 )); then
        echo -e "${C_YELLOW}>> Unmounting active filesystems on $dev...${C_RESET}"
        while IFS= read -r mp; do
            [[ -n "$mp" ]] || continue
            umount -R "$mp" 2>/dev/null || true
        done < <(printf '%s\n' "${!mount_targets[@]}" | awk '{print length "\t" $0}' | sort -rn | cut -f2-)
    fi

    # 3. Close active crypt mappers backed by this device tree
    while IFS= read -r node; do
        [[ -n "$node" ]] || continue
        crypts+=("$node")
    done < <(lsblk -pnro NAME,TYPE "$dev" 2>/dev/null | awk '$2=="crypt"{print $1}')

    if (( ${#crypts[@]} > 0 )); then
        echo -e "${C_YELLOW}>> Closing active LUKS containers on $dev...${C_RESET}"
        for (( i=${#crypts[@]}-1; i>=0; i-- )); do
            cryptsetup close "${crypts[i]##*/}" 2>/dev/null || true
        done
    fi

    udevadm settle
}

# --- Helper: Disk List ---
print_available_disks() {
    lsblk -d -e 7,11 -o NAME,SIZE,MODEL,TYPE,RO
    echo ""
}

# --- Shared: Secure LUKS Prompt ---
prompt_luks_password() {
    local pass1
    local pass2

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
    clear 2>/dev/null || true
    echo -e "${C_BOLD}=== AUTONOMOUS DISK PROVISIONING (${C_CYAN}${BOOT_MODE}${C_RESET}${C_BOLD}) ===${C_RESET}\n"

    print_available_disks

    read -r -p "Enter target drive to WIPE and PROVISION (e.g., nvme0n1): " raw_drive
    local target_input="/dev/${raw_drive#/dev/}"

    if [[ ! -b "$target_input" ]]; then
        echo -e "${C_RED}Critical: Block device $target_input not found. Aborting.${C_RESET}"
        exit 1
    fi

    local target_dev
    target_dev=$(readlink -f "$target_input")

    validate_target_disk "$target_dev"

    local luks_pass
    luks_pass=$(prompt_luks_password)

    echo -e "\n${C_RED}${C_BOLD}!!! WARNING: WIPING ALL DATA ON $target_dev IN 5 SECONDS !!!${C_RESET}"
    sleep 5

    teardown_device "$target_dev"
    ensure_mapper_name_available "$target_dev"

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

    partprobe "$target_dev"
    udevadm settle

    local part_boot
    local part_root
    part_boot=$(get_partition_path "$target_dev" 1)
    part_root=$(get_partition_path "$target_dev" 2)

    if ! wait_for_block_device "$part_root" 10; then
        echo -e "${C_RED}Critical: Root partition $part_root did not appear after partitioning. Aborting.${C_RESET}"
        exit 1
    fi

    if [[ "$BOOT_MODE" == "UEFI" ]] && ! wait_for_block_device "$part_boot" 10; then
        echo -e "${C_RED}Critical: EFI partition $part_boot did not appear after partitioning. Aborting.${C_RESET}"
        exit 1
    fi

    echo -e "${C_YELLOW}>> Clearing residual signatures on Root ($part_root)...${C_RESET}"
    wipefs -af "$part_root"

    echo -e "${C_YELLOW}>> Encrypting Root Partition ($part_root)...${C_RESET}"
    printf '%s' "$luks_pass" | cryptsetup -q --batch-mode luksFormat --type luks2 --key-file - "$part_root"
    printf '%s' "$luks_pass" | cryptsetup open --allow-discards --key-file - "$part_root" "$TARGET_CRYPT_NAME"
    OPENED_CRYPTROOT=1
    unset -v luks_pass

    echo -e "${C_YELLOW}>> Formatting Filesystems...${C_RESET}"
    mkfs.btrfs -f -L "ARCH_ROOT" "/dev/mapper/${TARGET_CRYPT_NAME}"

    if [[ "$BOOT_MODE" == "UEFI" ]]; then
        echo -e "${C_YELLOW}>> Clearing residual signatures on EFI ($part_boot)...${C_RESET}"
        wipefs -af "$part_boot"
        mkfs.fat -F 32 -n "EFI" "$part_boot"
    fi

    echo -e "${C_GREEN}>> Autonomous Provisioning Complete.${C_RESET}"
}

# --- Interactive Execution Flow ---
run_interactive_mode() {
    clear 2>/dev/null || true
    echo -e "${C_BOLD}=== INTERACTIVE DISK PROVISIONING (${C_CYAN}${BOOT_MODE}${C_RESET}${C_BOLD}) ===${C_RESET}\n"

    print_available_disks

    read -r -p "Enter drive to partition via cfdisk (e.g., nvme0n1): " raw_drive
    local target_input="/dev/${raw_drive#/dev/}"

    if [[ ! -b "$target_input" ]]; then
        echo -e "${C_RED}Critical: Block device $target_input not found. Aborting.${C_RESET}"
        exit 1
    fi

    local target_dev
    target_dev=$(readlink -f "$target_input")

    validate_target_disk "$target_dev"

    teardown_device "$target_dev"

    cfdisk "$target_dev" < /dev/tty > /dev/tty 2>&1
    partprobe "$target_dev"
    udevadm settle

    echo -e "\n${C_GREEN}>> Partitioning finished. Please specify the new layout.${C_RESET}"
    lsblk -o NAME,SIZE,TYPE,FSTYPE "$target_dev"

    read -r -p "Enter the new ROOT partition (e.g., nvme0n1p2): " raw_root
    local root_input="/dev/${raw_root#/dev/}"

    if [[ ! -b "$root_input" ]]; then
        echo -e "${C_RED}Critical: Root partition $root_input not found. Aborting.${C_RESET}"
        exit 1
    fi

    local part_root
    part_root=$(readlink -f "$root_input")
    validate_partition_on_target "$part_root" "$target_dev" "Root"

    local part_efi=""
    if [[ "$BOOT_MODE" == "UEFI" ]]; then
        read -r -p "Enter the new EFI partition (e.g., nvme0n1p1): " raw_efi
        local efi_input="/dev/${raw_efi#/dev/}"

        if [[ ! -b "$efi_input" ]]; then
            echo -e "${C_RED}Critical: EFI partition $efi_input not found. Aborting.${C_RESET}"
            exit 1
        fi

        part_efi=$(readlink -f "$efi_input")
        validate_partition_on_target "$part_efi" "$target_dev" "EFI"

        if [[ "$part_efi" == "$part_root" ]]; then
            echo -e "${C_RED}Critical: EFI and Root cannot be the same partition. Aborting.${C_RESET}"
            exit 1
        fi
    fi

    ensure_mapper_name_available "$target_dev"

    local luks_pass
    luks_pass=$(prompt_luks_password)

    echo -e "${C_YELLOW}>> Clearing residual signatures on Root ($part_root)...${C_RESET}"
    wipefs -af "$part_root"

    echo -e "${C_YELLOW}>> Encrypting Root Partition ($part_root)...${C_RESET}"
    printf '%s' "$luks_pass" | cryptsetup -q --batch-mode luksFormat --type luks2 --key-file - "$part_root"
    printf '%s' "$luks_pass" | cryptsetup open --allow-discards --key-file - "$part_root" "$TARGET_CRYPT_NAME"
    OPENED_CRYPTROOT=1
    unset -v luks_pass

    echo -e "${C_YELLOW}>> Formatting Root (BTRFS)...${C_RESET}"
    mkfs.btrfs -f -L "ARCH_ROOT" "/dev/mapper/${TARGET_CRYPT_NAME}"

    if [[ "$BOOT_MODE" == "UEFI" ]]; then
        echo -e "${C_YELLOW}>> Clearing residual signatures on EFI ($part_efi)...${C_RESET}"
        wipefs -af "$part_efi"

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
