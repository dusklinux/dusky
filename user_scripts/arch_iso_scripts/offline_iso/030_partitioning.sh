#!/usr/bin/env bash
# ==============================================================================
# MODULE: 030_partitioning.sh
# CONTEXT: Arch ISO Environment
# PURPOSE: Block Device Prep, GPT, Encryption Setup, Base Filesystem Creation
# ==============================================================================

set -euo pipefail

# Visual Constants
readonly C_BOLD=$'\033[1m'
readonly C_RED=$'\033[31m'
readonly C_GREEN=$'\033[32m'
readonly C_YELLOW=$'\033[33m'
readonly C_CYAN=$'\033[36m'
readonly C_MAGENTA=$'\033[35m'
readonly C_RESET=$'\033[0m'

readonly TARGET_CRYPT_NAME="cryptroot"
OPENED_CRYPTROOT=0

# --- Signal Handling & Cleanup ---
cleanup() {
    local status=${1:-0}

    trap - EXIT INT TERM

    # If this run opened cryptroot but failed later, close it.
    # On success, keep it open for the next module.
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

# --- Credential Ingestion (Phase 1) ---
if [[ -f "./.arch_credentials" ]]; then
    source "./.arch_credentials"
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

# --- Helper: Normalize findmnt Source ---
normalize_mount_source() {
    local src="${1:-}"
    printf '%s\n' "${src%%[*}"
}

# --- Helper: Get dm-crypt mapper name from node ---
get_dm_name() {
    local node="$1"
    local resolved

    resolved=$(readlink -f "$node")

    if [[ "$node" == /dev/mapper/* ]]; then
        printf '%s\n' "${node##*/}"
        return 0
    fi

    if [[ "$resolved" == /dev/dm-* && -r "/sys/class/block/${resolved##*/}/dm/name" ]]; then
        cat "/sys/class/block/${resolved##*/}/dm/name"
        return 0
    fi

    return 1
}

# --- Helper: Get immediate backing device ---
get_immediate_backing_device() {
    local node="$1"
    local parent=""
    local dm_name=""
    local backing=""
    local slave=""

    node=$(readlink -f "$node")

    parent=$(lsblk -ndlo PKNAME "$node" 2>/dev/null | head -n1 || true)
    if [[ -n "$parent" ]]; then
        printf '/dev/%s\n' "$parent"
        return 0
    fi

    if dm_name=$(get_dm_name "$node" 2>/dev/null); then
        backing=$(cryptsetup status "$dm_name" 2>/dev/null | awk -F': *' '$1 ~ /^[[:space:]]*device$/ {print $2; exit}' || true)
        if [[ -n "$backing" && -b "$backing" ]]; then
            readlink -f "$backing"
            return 0
        fi
    fi

    if [[ -d "/sys/class/block/${node##*/}/slaves" ]]; then
        slave=$(find "/sys/class/block/${node##*/}/slaves" -mindepth 1 -maxdepth 1 -printf '/dev/%f\n' -quit 2>/dev/null || true)
        if [[ -n "$slave" && -b "$slave" ]]; then
            readlink -f "$slave"
            return 0
        fi
    fi

    return 1
}

# --- Helper: Is Node Backed by Target Disk? ---
device_is_on_disk() {
    local node
    local disk
    local next

    node=$(readlink -f "$1")
    disk=$(readlink -f "$2")

    [[ -b "$node" && -b "$disk" ]] || return 1

    while true; do
        [[ "$node" == "$disk" ]] && return 0

        next=$(get_immediate_backing_device "$node" 2>/dev/null || true)
        [[ -n "$next" && -b "$next" ]] || return 1

        node="$next"
    done
}

# --- Helper: Resolve Swap Backing Device ---
get_swap_backing_device() {
    local swap_name="$1"
    local swap_src=""

    if [[ -b "$swap_name" ]]; then
        readlink -f "$swap_name"
        return 0
    fi

    swap_src=$(findmnt -rn -T "$swap_name" -o SOURCE 2>/dev/null | head -n1 || true)
    swap_src=$(normalize_mount_source "$swap_src")

    if [[ -n "$swap_src" && -e "$swap_src" ]]; then
        readlink -f "$swap_src" 2>/dev/null || printf '%s\n' "$swap_src"
    fi
}

# --- Helper: Does Device Tree Still Have Active Swap? ---
has_active_swap_on_device() {
    local dev="$1"
    local swap_name
    local swap_src

    while IFS= read -r swap_name; do
        [[ -n "$swap_name" ]] || continue
        swap_src=$(get_swap_backing_device "$swap_name")

        if [[ -n "$swap_src" && -b "$swap_src" ]] && device_is_on_disk "$swap_src" "$dev"; then
            return 0
        fi
    done < <(swapon --show=NAME --noheadings 2>/dev/null || true)

    return 1
}

# --- Helper: Does Device Tree Still Have Active Mounts? ---
has_active_mounts_on_device() {
    local dev="$1"
    local src
    local mp
    local norm_src

    while read -r src mp; do
        [[ -n "$src" && -n "$mp" ]] || continue
        norm_src=$(normalize_mount_source "$src")

        if [[ -b "$norm_src" ]] && device_is_on_disk "$norm_src" "$dev"; then
            return 0
        fi
    done < <(findmnt -rn -o SOURCE,TARGET 2>/dev/null || true)

    return 1
}

# --- Helper: Does Device Tree Still Have Active Crypt Mappings? ---
has_active_crypt_on_device() {
    local dev="$1"
    local node
    local type

    while read -r node type; do
        [[ -n "$node" && -n "$type" ]] || continue
        [[ "$type" == "crypt" ]] && return 0
    done < <(lsblk -pnlro NAME,TYPE "$dev" 2>/dev/null || true)

    return 1
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

    dev_type=$(lsblk -ndlo TYPE "$dev" 2>/dev/null | head -n1 || true)
    ro=$(lsblk -ndlo RO "$dev" 2>/dev/null | head -n1 || true)

    if [[ "$dev_type" != "disk" ]]; then
        echo -e "${C_RED}Critical: $dev is not a whole disk. Aborting.${C_RESET}"
        exit 1
    fi

    if [[ "$ro" != "0" ]]; then
        echo -e "${C_RED}Critical: $dev is read-only. Aborting.${C_RESET}"
        exit 1
    fi

    # Protect the live Arch ISO boot media
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

    part_type=$(lsblk -ndlo TYPE "$part" 2>/dev/null | head -n1 || true)
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

            if [[ -b "/dev/mapper/${TARGET_CRYPT_NAME}" ]]; then
                echo -e "${C_RED}Critical: Failed to release existing ${TARGET_CRYPT_NAME} mapper. Aborting.${C_RESET}"
                exit 1
            fi
        else
            echo -e "${C_RED}Critical: /dev/mapper/${TARGET_CRYPT_NAME} already exists elsewhere. Aborting.${C_RESET}"
            exit 1
        fi
    fi
}

# --- Helper: Teardown Active Disk Locks ---
teardown_device() {
    local dev="$1"
    local swap_name
    local swap_src
    local src
    local mp
    local node
    local type
    local i

    local -A mount_targets=()
    local -a crypts=()

    # 1. Disable active swap
    while IFS= read -r swap_name; do
        [[ -n "$swap_name" ]] || continue
        swap_src=$(get_swap_backing_device "$swap_name")

        if [[ -n "$swap_src" && -b "$swap_src" ]] && device_is_on_disk "$swap_src" "$dev"; then
            echo -e "${C_YELLOW}>> Disabling active swap on $dev...${C_RESET}"
            swapoff "$swap_name" 2>/dev/null || true
        fi
    done < <(swapon --show=NAME --noheadings 2>/dev/null || true)

    if has_active_swap_on_device "$dev"; then
        echo -e "${C_RED}Critical: Failed to disable active swap. Aborting.${C_RESET}"
        exit 1
    fi

    # 2. Unmount filesystems
    while read -r src mp; do
        [[ -n "$src" && -n "$mp" ]] || continue
        src=$(normalize_mount_source "$src")

        if [[ -b "$src" ]] && device_is_on_disk "$src" "$dev"; then
            mount_targets["$mp"]=1
        fi
    done < <(findmnt -rn -o SOURCE,TARGET 2>/dev/null || true)

    if (( ${#mount_targets[@]} > 0 )); then
        echo -e "${C_YELLOW}>> Unmounting active filesystems on $dev...${C_RESET}"
        while IFS= read -r mp; do
            [[ -n "$mp" ]] || continue
            umount "$mp" 2>/dev/null || umount -R "$mp" 2>/dev/null || true
        done < <(printf '%s\n' "${!mount_targets[@]}" | awk '{print length "\t" $0}' | sort -rn | cut -f2-)
    fi

    if has_active_mounts_on_device "$dev"; then
        echo -e "${C_RED}Critical: Failed to unmount active filesystems. Aborting.${C_RESET}"
        exit 1
    fi

    # 3. Close LUKS containers
    while read -r node type; do
        [[ -n "$node" && -n "$type" ]] || continue
        [[ "$type" == "crypt" ]] || continue
        crypts+=("$node")
    done < <(lsblk -pnlro NAME,TYPE "$dev" 2>/dev/null || true)

    if (( ${#crypts[@]} > 0 )); then
        echo -e "${C_YELLOW}>> Closing active LUKS containers on $dev...${C_RESET}"
        for (( i=${#crypts[@]}-1; i>=0; i-- )); do
            cryptsetup close "${crypts[i]##*/}" 2>/dev/null || true
        done
    fi

    udevadm settle

    if has_active_crypt_on_device "$dev"; then
        echo -e "${C_RED}Critical: Failed to close active LUKS containers. Aborting.${C_RESET}"
        exit 1
    fi
}

# --- Helper: Disk List ---
print_available_disks() {
    lsblk -d -l -e 7,11 -o NAME,SIZE,MODEL,TYPE,RO
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

# --- Unified Provisioning Flow ---
run_provisioning_wizard() {
    local cli_mode=""
    local force_encrypt=""
    
    # CLI Argument Parsing
    for arg in "$@"; do
        case "${arg,,}" in
            --auto|auto) cli_mode="auto" ;;
            --manual|manual) cli_mode="manual" ;;
            --rescue|rescue) cli_mode="rescue" ;;
            --encrypt) force_encrypt="1" ;;
            --no-encrypt) force_encrypt="0" ;;
        esac
    done
    
    clear 2>/dev/null || true
    echo -e "${C_BOLD}=== SYSTEM DISK PROVISIONING (${C_CYAN}${BOOT_MODE}${C_RESET}${C_BOLD}) ===${C_RESET}\n"

    print_available_disks

    read -r -p "Enter target drive to PROVISION/RESCUE (e.g., nvme0n1): " raw_drive
    local target_input="/dev/${raw_drive#/dev/}"

    if [[ ! -b "$target_input" ]]; then
        echo -e "${C_RED}Critical: Block device $target_input not found. Aborting.${C_RESET}"
        exit 1
    fi

    local target_dev
    target_dev=$(readlink -f "$target_input")
    validate_target_disk "$target_dev"

    # --- Strategy Selection Menu & CLI Override ---
    local strategy_choice=""
    
    if [[ "$cli_mode" == "auto" ]]; then
        echo -e "\n${C_YELLOW}>> [--auto] flag detected. Bypassing menu and defaulting to 'Wipe Entire Drive' strategy.${C_RESET}"
        strategy_choice="1"
    elif [[ "$cli_mode" == "manual" ]]; then
        echo -e "\n${C_YELLOW}>> [--manual] flag detected. Bypassing menu and defaulting to 'Manual Partitioning' strategy.${C_RESET}"
        strategy_choice="3"
    elif [[ "$cli_mode" == "rescue" ]]; then
        echo -e "\n${C_YELLOW}>> [--rescue] flag detected. Bypassing menu and defaulting to 'Rescue / Chroot' strategy.${C_RESET}"
        strategy_choice="4"
    else
        echo -e "\n${C_CYAN}Partitioning Strategies:${C_RESET}"
        echo -e "  [1] Wipe Entire Drive     (Default - Erases all data and creates standard layout)"
        echo -e "  [2] Select Existing       (Dual Boot - Retains other partitions, overwrites selected)"
        echo -e "  [3] Manual Partitioning   (Advanced - Opens cfdisk to let you design layout manually)"
        echo -e "  [4] Rescue / Chroot       (Mount Only - Unlocks LUKS or maps plain root without formatting)"
        echo ""
        read -r -p "Enter your choice [1/2/3/4]: " strategy_choice
    fi

    local wipe_entire_disk=0
    local manual_partition=0
    local rescue_mode=0
    local format_efi=1
    local part_boot=""
    local part_root=""

    case "$strategy_choice" in
        2)
            wipe_entire_disk=0
            manual_partition=0
            rescue_mode=0
            ;;
        3)
            wipe_entire_disk=0
            manual_partition=1
            rescue_mode=0
            ;;
        4)
            wipe_entire_disk=0
            manual_partition=0
            rescue_mode=1
            ;;
        *)
            wipe_entire_disk=1
            manual_partition=0
            rescue_mode=0
            ;;
    esac

    # --- Encryption Strategy Resolution ---
    local do_encrypt=1
    if [[ -n "$force_encrypt" ]]; then
        do_encrypt="$force_encrypt"
    elif [[ -n "${ENCRYPT_ROOT:-}" ]]; then
        do_encrypt="$ENCRYPT_ROOT"
    elif (( rescue_mode == 0 )); then
        echo -e "\n${C_CYAN}Disk Encryption:${C_RESET}"
        read -r -p "Encrypt the root partition with LUKS2? [Y/n]: " enc_choice
        if [[ "${enc_choice,,}" == "n" || "${enc_choice,,}" == "no" ]]; then
            do_encrypt=0
        else
            do_encrypt=1
        fi
    fi

    # Step 1: Strategy-Specific Pre-Work
    if (( manual_partition == 1 )); then
        echo -e "\n${C_YELLOW}>> Releasing disk locks before opening manual partitioner...${C_RESET}"
        teardown_device "$target_dev"
        
        echo -e "${C_YELLOW}>> Launching cfdisk...${C_RESET}"
        cfdisk "$target_dev" < /dev/tty > /dev/tty 2>&1
        
        partprobe "$target_dev"
        udevadm settle

        echo -e "\n${C_GREEN}>> Manual partitioning finished. Please specify your target layout.${C_RESET}"
        lsblk -l -o NAME,SIZE,TYPE,FSTYPE,PARTLABEL "$target_dev"
        echo ""
    elif (( wipe_entire_disk == 0 )); then
        echo -e "\n${C_CYAN}Available partitions on $target_dev:${C_RESET}"
        lsblk -l -o NAME,SIZE,TYPE,FSTYPE,PARTLABEL "$target_dev"
        echo ""
    fi

    # Step 2: Assign Partitions
    if (( wipe_entire_disk == 0 )); then
        read -r -p "Enter the ROOT partition (e.g., nvme0n1p2): " raw_root
        local root_input="/dev/${raw_root#/dev/}"
        if [[ ! -b "$root_input" ]]; then
            echo -e "${C_RED}Critical: Root partition $root_input not found. Aborting.${C_RESET}"
            exit 1
        fi
        part_root=$(readlink -f "$root_input")
        validate_partition_on_target "$part_root" "$target_dev" "Root"

        # EFI mapping is skipped in Rescue mode; 040_disk_mount.sh handles EFI interactively anyway
        if [[ "$BOOT_MODE" == "UEFI" && "$rescue_mode" == 0 ]]; then
            read -r -p "Enter the EFI partition (e.g., nvme0n1p1): " raw_efi
            local efi_input="/dev/${raw_efi#/dev/}"
            if [[ ! -b "$efi_input" ]]; then
                echo -e "${C_RED}Critical: EFI partition $efi_input not found. Aborting.${C_RESET}"
                exit 1
            fi
            part_boot=$(readlink -f "$efi_input")
            validate_partition_on_target "$part_boot" "$target_dev" "EFI"

            if [[ "$part_boot" == "$part_root" ]]; then
                echo -e "${C_RED}Critical: EFI and Root cannot be the same partition. Aborting.${C_RESET}"
                exit 1
            fi

            # Safe formatting logic for Dual Boot and Manual
            read -r -p "Format this EFI partition? (Say 'n' if sharing with Windows) [y/N]: " fmt_choice
            if [[ "${fmt_choice,,}" != "y" && "${fmt_choice,,}" != "yes" ]]; then
                format_efi=0
            fi
        fi
    fi

    # Step 3: Rescue Mode Early Exit
    if (( rescue_mode == 1 )); then
        echo -e "\n${C_YELLOW}>> Rescue Mode selected. No data will be formatted.${C_RESET}"
        
        local state_file="/tmp/arch_install_state.env"
        > "$state_file"
        echo "PROVISIONED_ROOT_PART=\"$part_root\"" >> "$state_file"

        # Validation Check: Ensure the selected partition actually contains a LUKS header
        if ! cryptsetup isLuks "$part_root" >/dev/null 2>&1; then
            echo -e "${C_YELLOW}>> Partition $part_root does not contain a valid LUKS header. Assuming unencrypted plain partition.${C_RESET}"
            echo "ENCRYPT_ROOT=\"0\"" >> "$state_file"
            echo -e "${C_GREEN}>> Rescue setup complete. Proceed to 040_disk_mount.sh to map subvolumes.${C_RESET}"
            return 0
        fi
        
        teardown_device "$target_dev"
        ensure_mapper_name_available "$target_dev"

        echo -e "${C_YELLOW}>> Unlocking existing LUKS Root Partition ($part_root)...${C_RESET}"
        
        # Modified for Auto-Rescue support
        if [[ -n "${ROOT_PASS:-}" ]]; then
            printf '%s' "$ROOT_PASS" | cryptsetup open --allow-discards --key-file - "$part_root" "$TARGET_CRYPT_NAME"
        else
            cryptsetup open --allow-discards "$part_root" "$TARGET_CRYPT_NAME"
        fi
        
        OPENED_CRYPTROOT=1
        
        echo "ENCRYPT_ROOT=\"1\"" >> "$state_file"
        echo -e "${C_GREEN}>> Rescue unlocked. Proceed to 040_disk_mount.sh to map subvolumes without formatting.${C_RESET}"
        return 0
    fi

    # Step 4: Authentication (For new/overwritten systems)
    local luks_pass=""
    if (( do_encrypt == 1 )); then
        if [[ -n "${ROOT_PASS:-}" ]]; then
            echo -e "${C_YELLOW}>> Inheriting LUKS passphrase from staged credentials...${C_RESET}"
            luks_pass="$ROOT_PASS"
        else
            luks_pass=$(prompt_luks_password)
        fi
    else
        echo -e "${C_YELLOW}>> Encryption disabled. Skipping LUKS password setup.${C_RESET}"
    fi

    # Step 5: Final Confirmation Delay
    echo -e "\n${C_MAGENTA}${C_BOLD}>>> Applying configurations in 5 seconds... <<<${C_RESET}"
    
    if (( wipe_entire_disk == 1 )); then
        echo -e "${C_YELLOW}Action: Re-partitioning and formatting the entire drive (${C_CYAN}$target_dev${C_YELLOW}).${C_RESET}"
    else
        echo -e "${C_YELLOW}Action: Formatting the selected partitions:${C_RESET}"
        echo -e "  ${C_BOLD}*${C_RESET} Root: ${C_CYAN}$part_root${C_RESET}"
        if [[ "$BOOT_MODE" == "UEFI" && -n "${part_boot:-}" ]]; then
            if (( format_efi == 1 )); then
                echo -e "  ${C_BOLD}*${C_RESET} EFI:  ${C_CYAN}$part_boot${C_RESET} (Will be formatted)"
            else
                echo -e "  ${C_BOLD}*${C_RESET} EFI:  ${C_CYAN}$part_boot${C_RESET} (Data will be preserved)"
            fi
        fi
    fi
    
    echo -e "${C_MAGENTA}Press Ctrl+C to abort, or simply wait to continue.${C_RESET}"
    sleep 5

    # Step 6: Master Teardown & Validation
    teardown_device "$target_dev"
    ensure_mapper_name_available "$target_dev"

    # Step 7: Drive Wipe & Re-partition (Strategy 1 Only)
    if (( wipe_entire_disk == 1 )); then
        local root_part_type="8304" # Standard Linux root (x86-64)
        if (( do_encrypt == 1 )); then
            root_part_type="8309" # Linux LUKS
        fi

        echo -e "${C_YELLOW}>> Zapping partition table...${C_RESET}"
        wipefs -a "$target_dev"
        sgdisk --zap-all "$target_dev"

        echo -e "${C_YELLOW}>> Writing new GPT layout...${C_RESET}"
        if [[ "$BOOT_MODE" == "UEFI" ]]; then
            sgdisk -n 1:0:+1.5G -t 1:ef00 -c 1:"EFI System" "$target_dev"
            sgdisk -n 2:0:0   -t 2:"$root_part_type" -c 2:"Linux Root" "$target_dev"
        else
            sgdisk -n 1:0:+1M -t 1:ef02 -c 1:"BIOS Boot"  "$target_dev"
            sgdisk -n 2:0:0   -t 2:"$root_part_type" -c 2:"Linux Root" "$target_dev"
        fi

        partprobe "$target_dev"
        udevadm settle

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
    fi

    # Step 8: Shared Architecture Formatting
    if cryptsetup isLuks "$part_root" >/dev/null 2>&1; then
        echo -e "${C_YELLOW}>> Cryptographically erasing existing LUKS header on $part_root...${C_RESET}"
        cryptsetup erase "$part_root" 2>/dev/null || true
    fi

    echo -e "${C_YELLOW}>> Clearing residual signatures on Root ($part_root)...${C_RESET}"
    wipefs -af "$part_root"

    local btrfs_target="$part_root"

    if (( do_encrypt == 1 )); then
        echo -e "${C_YELLOW}>> Encrypting Root Partition ($part_root)...${C_RESET}"
        printf '%s' "$luks_pass" | cryptsetup --batch-mode luksFormat --type luks2 --key-file - "$part_root"
        printf '%s' "$luks_pass" | cryptsetup open --allow-discards --key-file - "$part_root" "$TARGET_CRYPT_NAME"
        OPENED_CRYPTROOT=1
        unset -v luks_pass
        btrfs_target="/dev/mapper/${TARGET_CRYPT_NAME}"
    fi

    echo -e "${C_YELLOW}>> Formatting Root (BTRFS with blake2 csum and advanced features)...${C_RESET}"
    mkfs.btrfs -f --csum blake2 -O no-holes,free-space-tree,block-group-tree -L "ARCH_ROOT" "$btrfs_target"

    if [[ "$BOOT_MODE" == "UEFI" ]]; then
        if (( format_efi == 1 )); then
            echo -e "${C_YELLOW}>> Clearing residual signatures and Formatting EFI ($part_boot)...${C_RESET}"
            wipefs -af "$part_boot"
            mkfs.fat -F 32 -n "EFI" "$part_boot"
        else
            echo -e "${C_YELLOW}>> Skipping EFI format to preserve existing bootloaders.${C_RESET}"
        fi
    fi

    # --- State Export for 040_disk_mount.sh ---
    local state_file="/tmp/arch_install_state.env"
    > "$state_file"
    if [[ -n "${part_root:-}" ]]; then
        echo "PROVISIONED_ROOT_PART=\"$part_root\"" >> "$state_file"
    fi
    if [[ -n "${part_boot:-}" ]]; then
        echo "PROVISIONED_EFI_PART=\"$part_boot\"" >> "$state_file"
    fi
    echo "ENCRYPT_ROOT=\"$do_encrypt\"" >> "$state_file"

    echo -e "${C_GREEN}>> Disk Provisioning Complete. Ready for architecture assembly.${C_RESET}"
}

# --- Entry Logic ---
run_provisioning_wizard "$@"

exit 0
