#!/usr/bin/env bash
# ==============================================================================
# MODULE: 040_disk_mount.sh
# CONTEXT: Arch ISO Environment
# PURPOSE: BTRFS Subvolume Generation, NOCOW Attributes, and FHS Mounting
# ==============================================================================

set -euo pipefail

readonly C_BOLD=$'\033[1m'
readonly C_RED=$'\033[31m'
readonly C_GREEN=$'\033[32m'
readonly C_YELLOW=$'\033[33m'
readonly C_CYAN=$'\033[36m'
readonly C_RESET=$'\033[0m'

readonly TEMP_MNT="/mnt/btrfs_temp"
readonly SWAPFILE_PATH="/mnt/swap/swapfile"
readonly SWAPFILE_SIZE_BYTES=4294967296
readonly EFI_GPT_TYPE="c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
readonly BTRFS_OPTS="rw,noatime,compress=zstd:3,space_cache=v2,discard=async"

MAPPED_ROOT=""
SUCCESS=0
EFI_PART=""
ROOT_PART=""
ROOT_DISK=""

if [[ -d /sys/firmware/efi/efivars ]]; then
    readonly BOOT_MODE="UEFI"
else
    readonly BOOT_MODE="BIOS"
fi

# --- Signal Handling & Cleanup ---
unmount_mount_tree() {
    local mount_targets=""

    mount_targets=$(
        findmnt -rn -o TARGET 2>/dev/null \
        | awk '$0=="/mnt" || index($0,"/mnt/")==1' \
        | awk '{print length "\t" $0}' \
        | sort -rn \
        | cut -f2- || true
    )

    if [[ -n "$mount_targets" ]]; then
        while IFS= read -r mp; do
            [[ -n "$mp" ]] || continue
            umount "$mp" 2>/dev/null || umount -R "$mp" 2>/dev/null || true
        done <<< "$mount_targets"
    fi
}

cleanup() {
    local status=${1:-0}
    trap - EXIT INT TERM

    if (( status != 0 )) && (( SUCCESS == 0 )); then
        if swapon --show=NAME --noheadings 2>/dev/null | grep -Fxq "$SWAPFILE_PATH"; then
            swapoff "$SWAPFILE_PATH" 2>/dev/null || true
        fi
        unmount_mount_tree
    fi

    rm -rf "$TEMP_MNT" 2>/dev/null || true
    printf '%b\n' "$C_RESET"
    exit "$status"
}

trap 'cleanup "$?"' EXIT
trap 'cleanup 130' INT
trap 'cleanup 143' TERM

# --- Helpers ---
get_partition_path() {
    local dev_path="$1"
    local num="$2"
    local dev_name="${dev_path#/dev/}"

    if [[ "$dev_name" =~ ^(nvme|mmcblk|loop) ]]; then
        printf '%s\n' "${dev_path}p${num}"
    else
        printf '%s\n' "${dev_path}${num}"
    fi
}

is_empty_dir() {
    local dir="$1"
    if find "$dir" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null | grep -q .; then
        return 1
    fi
    return 0
}

ensure_subvolume() {
    local path="$1"
    local nocow="${2:-0}"
    local existed=0

    if [[ -e "$path" ]]; then
        if btrfs subvolume show "$path" >/dev/null 2>&1; then
            existed=1
        else
            echo -e "${C_RED}Critical: $path exists but is not a Btrfs subvolume. Aborting.${C_RESET}"
            exit 1
        fi
    else
        btrfs subvolume create "$path" >/dev/null
    fi

    if [[ "$nocow" == "1" ]]; then
        if (( existed == 0 )); then
            chattr +C "$path"
        elif is_empty_dir "$path"; then
            chattr +C "$path" 2>/dev/null || true
        fi
    fi
}

teardown_state() {
    if swapon --show=NAME --noheadings 2>/dev/null | grep -Fxq "$SWAPFILE_PATH"; then
        swapoff "$SWAPFILE_PATH"
    fi

    unmount_mount_tree
    rm -rf "$TEMP_MNT" 2>/dev/null || true
}

# --- Root Device Resolution ---
determine_root_partition() {
    local auto_mode="$1"

    if [[ -b "/dev/mapper/cryptroot" ]]; then
        MAPPED_ROOT="/dev/mapper/cryptroot"
        local mapped_name="${MAPPED_ROOT##*/}"
        local backing_part=""

        backing_part=$(cryptsetup status "$mapped_name" 2>/dev/null | awk -F': *' '
            $1 ~ /^[[:space:]]*device$/ { print $2; exit }
        ' || true)

        if [[ -z "$backing_part" ]]; then
            echo -e "${C_RED}Critical: Failed to determine the encrypted root partition behind $MAPPED_ROOT.${C_RESET}"
            exit 1
        fi

        ROOT_PART=$(readlink -f "$backing_part")
    else
        echo -e "${C_YELLOW}>> /dev/mapper/cryptroot not found. Assuming unencrypted root.${C_RESET}"
        if (( auto_mode == 1 )); then
            local -a btrfs_parts=()
            local part fstype
            while read -r part fstype; do
                [[ "$fstype" == "btrfs" ]] && btrfs_parts+=("$part")
            done < <(lsblk -pnro NAME,FSTYPE 2>/dev/null || true)

            if (( ${#btrfs_parts[@]} == 1 )); then
                ROOT_PART=$(readlink -f "${btrfs_parts[0]}")
                MAPPED_ROOT="$ROOT_PART"
                echo -e "${C_CYAN}Auto-detected unencrypted BTRFS root: $ROOT_PART${C_RESET}"
            else
                echo -e "${C_RED}Critical: Could not auto-detect a unique unencrypted BTRFS root partition. Please run interactively.${C_RESET}"
                exit 1
            fi
        else
            echo -e "${C_CYAN}Available block devices:${C_RESET}"
            lsblk -o NAME,SIZE,TYPE,FSTYPE,PARTLABEL
            echo ""
            read -r -p "Enter your BTRFS root partition (e.g., nvme0n1p2): " raw_root
            ROOT_PART=$(readlink -f "/dev/${raw_root#/dev/}")
            MAPPED_ROOT="$ROOT_PART"
        fi
    fi

    if [[ ! -b "$ROOT_PART" ]]; then
        echo -e "${C_RED}Critical: Root partition $ROOT_PART is not a valid block device.${C_RESET}"
        exit 1
    fi

    local root_disk_name=""
    root_disk_name=$(lsblk -ndo PKNAME "$ROOT_PART" 2>/dev/null | head -n1 || true)

    if [[ -z "$root_disk_name" ]]; then
        echo -e "${C_RED}Critical: Failed to determine the parent disk for $ROOT_PART.${C_RESET}"
        exit 1
    fi

    ROOT_DISK=$(readlink -f "/dev/${root_disk_name}")
    if [[ ! -b "$ROOT_DISK" ]]; then
        echo -e "${C_RED}Critical: Derived parent disk $ROOT_DISK is not a valid block device.${C_RESET}"
        exit 1
    fi
}

validate_root_state() {
    local root_fstype=""

    if [[ ! -b "$MAPPED_ROOT" ]]; then
        echo -e "${C_RED}Critical: $MAPPED_ROOT not found. Aborting.${C_RESET}"
        exit 1
    fi

    root_fstype=$(lsblk -ndo FSTYPE "$MAPPED_ROOT" 2>/dev/null | head -n1 || true)
    if [[ "$root_fstype" != "btrfs" ]]; then
        echo -e "${C_RED}Critical: $MAPPED_ROOT is not a Btrfs filesystem. Aborting.${C_RESET}"
        exit 1
    fi
}

# --- EFI Detection / Validation ---
validate_efi_partition() {
    local part="$1"
    local part_type=""
    local parent_name=""
    local parent_disk=""
    local fstype=""
    local parttype=""

    if [[ ! -b "$part" ]]; then
        echo -e "${C_RED}Critical: EFI partition $part not found or is not a block device.${C_RESET}"
        exit 1
    fi

    if [[ "$part" == "$ROOT_PART" ]]; then
        echo -e "${C_RED}Critical: EFI partition cannot be the same as the root partition.${C_RESET}"
        exit 1
    fi

    part_type=$(lsblk -ndo TYPE "$part" 2>/dev/null | head -n1 || true)
    if [[ "$part_type" != "part" ]]; then
        echo -e "${C_RED}Critical: EFI target $part is not a partition. Aborting.${C_RESET}"
        exit 1
    fi

    parent_name=$(lsblk -ndo PKNAME "$part" 2>/dev/null | head -n1 || true)
    if [[ -z "$parent_name" ]]; then
        echo -e "${C_RED}Critical: Failed to determine the parent disk for EFI partition $part.${C_RESET}"
        exit 1
    fi

    parent_disk=$(readlink -f "/dev/${parent_name}")
    if [[ "$parent_disk" != "$ROOT_DISK" ]]; then
        echo -e "${C_RED}Critical: EFI partition $part does not belong to the same disk as $MAPPED_ROOT.${C_RESET}"
        exit 1
    fi

    fstype=$(lsblk -ndo FSTYPE "$part" 2>/dev/null | head -n1 || true)
    parttype=$(lsblk -ndo PARTTYPE "$part" 2>/dev/null | head -n1 || true)

    if [[ "${parttype,,}" != "$EFI_GPT_TYPE" && "${fstype,,}" != "vfat" && "${fstype,,}" != "fat32" ]]; then
        echo -e "${C_RED}Critical: $part does not look like a valid EFI System Partition.${C_RESET}"
        exit 1
    fi
}

auto_detect_efi_partition() {
    local disk="$1"
    local -a guid_matches=()
    local -a label_matches=()
    local -a vfat_matches=()
    local -a non_root_parts=()
    local part=""
    local type=""
    local fstype=""
    local parttype=""
    local partlabel=""

    while read -r part type; do
        [[ "$type" == "part" ]] || continue
        part=$(readlink -f "$part")
        [[ "$part" == "$ROOT_PART" ]] && continue

        non_root_parts+=("$part")

        parttype=$(lsblk -ndo PARTTYPE "$part" 2>/dev/null | head -n1 || true)
        fstype=$(lsblk -ndo FSTYPE "$part" 2>/dev/null | head -n1 || true)
        partlabel=$(lsblk -ndo PARTLABEL "$part" 2>/dev/null | head -n1 || true)

        if [[ "${parttype,,}" == "$EFI_GPT_TYPE" ]]; then
            guid_matches+=("$part")
        fi

        if [[ "${partlabel,,}" == *efi* ]]; then
            label_matches+=("$part")
        fi

        if [[ "${fstype,,}" == "vfat" || "${fstype,,}" == "fat32" ]]; then
            vfat_matches+=("$part")
        fi
    done < <(lsblk -pnro NAME,TYPE "$disk" 2>/dev/null)

    if (( ${#guid_matches[@]} == 1 )); then
        printf '%s\n' "${guid_matches[0]}"
        return 0
    fi
    if (( ${#guid_matches[@]} > 1 )); then
        return 1
    fi

    if (( ${#label_matches[@]} == 1 )); then
        printf '%s\n' "${label_matches[0]}"
        return 0
    fi
    if (( ${#label_matches[@]} > 1 )); then
        return 1
    fi

    if (( ${#vfat_matches[@]} == 1 )); then
        printf '%s\n' "${vfat_matches[0]}"
        return 0
    fi
    if (( ${#vfat_matches[@]} > 1 )); then
        return 1
    fi

    if (( ${#non_root_parts[@]} == 1 )); then
        printf '%s\n' "${non_root_parts[0]}"
        return 0
    fi

    return 1
}

prompt_for_efi_partition() {
    local raw_efi=""

    echo -e "${C_CYAN}Available partitions on ${ROOT_DISK}:${C_RESET}"
    lsblk -o NAME,SIZE,TYPE,FSTYPE,PARTTYPE,PARTLABEL "$ROOT_DISK"
    read -r -p "Enter your EFI partition (e.g., nvme0n1p1): " raw_efi
    EFI_PART=$(readlink -f "/dev/${raw_efi#/dev/}")
}

determine_efi_partition() {
    local auto_mode="$1"
    local detected=""

    [[ "$BOOT_MODE" == "UEFI" ]] || return 0

    if (( auto_mode == 1 )); then
        detected=$(auto_detect_efi_partition "$ROOT_DISK" || true)
        if [[ -n "$detected" ]]; then
            EFI_PART=$(readlink -f "$detected")
            echo -e "${C_CYAN}Auto-detected EFI partition: $EFI_PART${C_RESET}"
        else
            echo -e "${C_YELLOW}>> Unable to auto-detect a unique EFI partition. User input required.${C_RESET}"
            prompt_for_efi_partition
        fi
    else
        prompt_for_efi_partition
    fi

    validate_efi_partition "$EFI_PART"
}

# --- Phase 1: Subvolume Matrix Generation ---
construct_subvolume_matrix() {
    echo -e "${C_YELLOW}>> Constructing Subvolume Matrix on Root...${C_RESET}"

    mkdir -p "$TEMP_MNT"
    mount -t btrfs -o subvolid=5 "$MAPPED_ROOT" "$TEMP_MNT"

    declare -a STD_SUBVOLS=(
        "@"
        "@home"
        "@snapshots"
        "@home_snapshots"
        "@var_log"
        "@var_cache"
        "@var_tmp"
        "@var_lib_machines"
        "@var_lib_portables"
    )

    declare -a NOCOW_SUBVOLS=(
        "@var_lib_libvirt"
        "@swap"
    )

    local sub=""
    for sub in "${STD_SUBVOLS[@]}"; do
        ensure_subvolume "${TEMP_MNT}/${sub}" 0
    done

    for sub in "${NOCOW_SUBVOLS[@]}"; do
        ensure_subvolume "${TEMP_MNT}/${sub}" 1
    done

    echo -e "${C_GREEN}>> Subvolume matrix verified.${C_RESET}"
    umount "$TEMP_MNT"
    rm -rf "$TEMP_MNT"
}

# --- Phase 2: FHS Hierarchy Assembly ---
assemble_fhs() {
    echo -e "${C_YELLOW}>> Assembling File Hierarchy Standard (FHS) to /mnt...${C_RESET}"

    mkdir -p /mnt
    mount -o "${BTRFS_OPTS},subvol=@" "$MAPPED_ROOT" /mnt

    mkdir -p /mnt/{home,.snapshots,var/log,var/cache,var/tmp,var/lib/machines,var/lib/portables,var/lib/libvirt,swap,boot}

    mount -o "${BTRFS_OPTS},subvol=@home"              "$MAPPED_ROOT" /mnt/home
    mount -o "${BTRFS_OPTS},subvol=@snapshots"         "$MAPPED_ROOT" /mnt/.snapshots
    mount -o "${BTRFS_OPTS},subvol=@var_log"           "$MAPPED_ROOT" /mnt/var/log
    mount -o "${BTRFS_OPTS},subvol=@var_cache"         "$MAPPED_ROOT" /mnt/var/cache
    mount -o "${BTRFS_OPTS},subvol=@var_tmp"           "$MAPPED_ROOT" /mnt/var/tmp
    mount -o "${BTRFS_OPTS},subvol=@var_lib_machines"  "$MAPPED_ROOT" /mnt/var/lib/machines
    mount -o "${BTRFS_OPTS},subvol=@var_lib_portables" "$MAPPED_ROOT" /mnt/var/lib/portables
    mount -o "${BTRFS_OPTS},subvol=@var_lib_libvirt"   "$MAPPED_ROOT" /mnt/var/lib/libvirt
    mount -o "${BTRFS_OPTS},subvol=@swap"              "$MAPPED_ROOT" /mnt/swap

    mkdir -p /mnt/home/.snapshots
    mount -o "${BTRFS_OPTS},subvol=@home_snapshots"    "$MAPPED_ROOT" /mnt/home/.snapshots

    if [[ "$BOOT_MODE" == "UEFI" ]]; then
        echo -e "${C_YELLOW}>> Mounting EFI ($EFI_PART) to /mnt/boot...${C_RESET}"
        mount "$EFI_PART" /mnt/boot
    fi
}

# --- Phase 3: Swapfile Initialization ---
initialize_swapfile() {
    local existing_size=""

    echo -e "${C_YELLOW}>> Ensuring 4GB Static Swapfile...${C_RESET}"

    if swapon --show=NAME --noheadings 2>/dev/null | grep -Fxq "$SWAPFILE_PATH"; then
        swapoff "$SWAPFILE_PATH"
    fi

    if [[ -e "$SWAPFILE_PATH" && ! -f "$SWAPFILE_PATH" ]]; then
        echo -e "${C_RED}Critical: $SWAPFILE_PATH exists but is not a regular file. Aborting.${C_RESET}"
        exit 1
    fi

    if [[ -f "$SWAPFILE_PATH" ]]; then
        existing_size=$(stat -Lc '%s' "$SWAPFILE_PATH" 2>/dev/null || true)
        if [[ "$existing_size" == "$SWAPFILE_SIZE_BYTES" ]]; then
            if swapon "$SWAPFILE_PATH" 2>/dev/null; then
                echo -e "${C_GREEN}>> Existing swapfile re-activated.${C_RESET}"
                return 0
            fi
        fi
        rm -f "$SWAPFILE_PATH"
    fi

    btrfs filesystem mkswapfile --size 4G --uuid clear "$SWAPFILE_PATH"
    swapon "$SWAPFILE_PATH"
}

# --- Main Flow ---
run_common() {
    local auto_mode="$1"

    teardown_state
    determine_root_partition "$auto_mode"
    validate_root_state
    determine_efi_partition "$auto_mode"
    construct_subvolume_matrix
    assemble_fhs
    initialize_swapfile

    SUCCESS=1
    echo -e "\n${C_GREEN}${C_BOLD}>> Setup Complete. System is primed for 'pacstrap'.${C_RESET}"
    lsblk -f "$ROOT_DISK" || true
}

run_auto_mode() {
    echo -e "${C_BOLD}=== AUTONOMOUS BTRFS ARCHITECTURE & MOUNTING (${C_CYAN}${BOOT_MODE}${C_RESET}${C_BOLD}) ===${C_RESET}\n"
    run_common 1
}

run_interactive_mode() {
    echo -e "${C_BOLD}=== INTERACTIVE BTRFS ARCHITECTURE & MOUNTING (${C_CYAN}${BOOT_MODE}${C_RESET}${C_BOLD}) ===${C_RESET}\n"
    run_common 0
}

# --- Entry Logic ---
if [[ "${1:-}" == "--auto" || "${1:-}" == "auto" ]]; then
    run_auto_mode
else
    read -r -p "Run AUTONOMOUS subvolume setup and mounting? [y/N]: " choice
    if [[ "${choice,,}" == "y" ]]; then
        run_auto_mode
    else
        run_interactive_mode
    fi
fi

exit 0
