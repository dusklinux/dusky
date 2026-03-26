#!/usr/bin/env bash
# ==============================================================================
# MODULE: 004_disk_mount.sh
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

if [[ -d /sys/firmware/efi/efivars ]]; then
    readonly BOOT_MODE="UEFI"
else
    readonly BOOT_MODE="BIOS"
fi

# --- Helper Functions ---
get_partition_path() {
    local dev_path="$1"
    local num="$2"
    local dev_name="${dev_path#/dev/}" # Strip /dev/ prefix for clean anchored regex validation
    
    # Strict anchored regex prevents false positives on complex topology names
    if [[ "$dev_name" =~ ^(nvme|mmcblk|loop) ]]; then
        printf '%s\n' "${dev_path}p${num}"
    else
        printf '%s\n' "${dev_path}${num}"
    fi
}

# --- Idempotent State Teardown ---
if swapon --show=NAME --noheadings | grep -Fxq '/mnt/swap/swapfile'; then
    swapoff /mnt/swap/swapfile
fi

if findmnt -Rno TARGET /mnt >/dev/null 2>&1; then
    umount -R /mnt
fi

# --- Validations ---
readonly MAPPED_ROOT="/dev/mapper/cryptroot"

if [[ ! -e "$MAPPED_ROOT" ]]; then
    echo -e "${C_RED}Critical: $MAPPED_ROOT not found. Did you run the partitioning module?${C_RESET}"
    exit 1
fi

echo -e "${C_BOLD}=== BTRFS ARCHITECTURE & MOUNTING ===${C_RESET}\n"

# --- Determine & Validate EFI Partition ---
EFI_PART=""
if [[ "$BOOT_MODE" == "UEFI" ]]; then
    # Auto Mode: Intelligently derive the EFI partition with strict multi-line pipeline safety
    if [[ "${1:-}" == "--auto" || "${1:-}" == "auto" ]]; then
        ROOT_PART_NAME=$(lsblk -no PKNAME "$MAPPED_ROOT" | head -n1)
        if [[ -z "$ROOT_PART_NAME" ]]; then
            echo -e "${C_RED}Critical: Failed to determine the encrypted root partition behind $MAPPED_ROOT.${C_RESET}"
            exit 1
        fi

        ROOT_DISK_NAME=$(lsblk -no PKNAME "/dev/${ROOT_PART_NAME}" | head -n1)
        if [[ -z "$ROOT_DISK_NAME" ]]; then
            echo -e "${C_RED}Critical: Failed to determine the parent disk for /dev/${ROOT_PART_NAME}.${C_RESET}"
            exit 1
        fi

        EFI_PART=$(get_partition_path "/dev/${ROOT_DISK_NAME}" 1)
        echo -e "${C_CYAN}Auto-detected EFI partition: $EFI_PART${C_RESET}"
    else
        # Interactive Mode
        lsblk -o NAME,SIZE,TYPE,FSTYPE | grep -i "vfat\|efi" || true
        read -r -p "Enter your EFI partition (e.g., nvme0n1p1): " raw_efi
        EFI_PART="/dev/${raw_efi#/dev/}"
    fi

    # FAIL-FAST: Validate block device immediately. Never mutate BTRFS state if this fails.
    if [[ ! -b "$EFI_PART" ]]; then
        echo -e "${C_RED}Critical: EFI partition $EFI_PART not found or is not a block device.${C_RESET}"
        exit 1
    fi
fi

# --- Phase 1: Subvolume Matrix Generation ---
echo -e "${C_YELLOW}>> Constructing Subvolume Matrix on Root...${C_RESET}"

readonly TEMP_MNT="/mnt/btrfs_temp"
mkdir -p "$TEMP_MNT"
mount -t btrfs "$MAPPED_ROOT" "$TEMP_MNT"

# Standard Snapshot-Aware Subvolumes
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

for sub in "${STD_SUBVOLS[@]}"; do
    btrfs subvolume create "${TEMP_MNT}/${sub}" >/dev/null
done

# NOCOW Subvolumes (VMs & Swap)
declare -a NOCOW_SUBVOLS=(
    "@var_lib_libvirt"
    "@swap"
)

for sub in "${NOCOW_SUBVOLS[@]}"; do
    btrfs subvolume create "${TEMP_MNT}/${sub}" >/dev/null
    # CRITICAL: Apply NOCOW (+C) strictly while the directory is empty
    chattr +C "${TEMP_MNT}/${sub}"
done

echo -e "${C_GREEN}>> Matrix generated and attributes securely applied.${C_RESET}"
umount "$TEMP_MNT"
rm -rf "$TEMP_MNT"

# --- Phase 2: FHS Hierarchy Assembly ---
# Optimized BTRFS Mount Options (Includes discard=async for NVMe I/O performance)
readonly BTRFS_OPTS="rw,noatime,compress=zstd:3,space_cache=v2,discard=async"

echo -e "${C_YELLOW}>> Assembling File Hierarchy Standard (FHS) to /mnt...${C_RESET}"

# 1. Mount Top Level Root First
mount -o "${BTRFS_OPTS},subvol=@" "$MAPPED_ROOT" /mnt

# 2. Generate Mountpoints
mkdir -p /mnt/{home,.snapshots,var/log,var/cache,var/tmp,var/lib/machines,var/lib/portables,var/lib/libvirt,swap,boot}

# 3. Mount Sub-branches
mount -o "${BTRFS_OPTS},subvol=@home"               "$MAPPED_ROOT" /mnt/home
mount -o "${BTRFS_OPTS},subvol=@snapshots"          "$MAPPED_ROOT" /mnt/.snapshots
mount -o "${BTRFS_OPTS},subvol=@var_log"            "$MAPPED_ROOT" /mnt/var/log
mount -o "${BTRFS_OPTS},subvol=@var_cache"          "$MAPPED_ROOT" /mnt/var/cache
mount -o "${BTRFS_OPTS},subvol=@var_tmp"            "$MAPPED_ROOT" /mnt/var/tmp
mount -o "${BTRFS_OPTS},subvol=@var_lib_machines"   "$MAPPED_ROOT" /mnt/var/lib/machines
mount -o "${BTRFS_OPTS},subvol=@var_lib_portables"  "$MAPPED_ROOT" /mnt/var/lib/portables
mount -o "${BTRFS_OPTS},subvol=@var_lib_libvirt"    "$MAPPED_ROOT" /mnt/var/lib/libvirt
mount -o "${BTRFS_OPTS},subvol=@swap"               "$MAPPED_ROOT" /mnt/swap

# Nested Home Snapshots
mkdir -p /mnt/home/.snapshots
mount -o "${BTRFS_OPTS},subvol=@home_snapshots"     "$MAPPED_ROOT" /mnt/home/.snapshots

# 4. Mount EFI Target
if [[ "$BOOT_MODE" == "UEFI" ]]; then
    echo -e "${C_YELLOW}>> Mounting EFI ($EFI_PART) to /mnt/boot...${C_RESET}"
    mount "$EFI_PART" /mnt/boot
fi

# --- Phase 3: Swapfile Initialization ---
echo -e "${C_YELLOW}>> Generating 4GB Static Swapfile...${C_RESET}"
# Modern 'mkswapfile' properly formats the blocks avoiding COW fragmentation entirely
btrfs filesystem mkswapfile --size 4G --uuid clear /mnt/swap/swapfile
swapon /mnt/swap/swapfile

echo -e "\n${C_GREEN}${C_BOLD}>> Setup Complete. System is primed for 'pacstrap'.${C_RESET}"
lsblk -f "$MAPPED_ROOT"
exit 0
