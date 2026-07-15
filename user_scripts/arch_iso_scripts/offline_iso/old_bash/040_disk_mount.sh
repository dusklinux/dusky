#!/usr/bin/env bash
# ==============================================================================
# MODULE: 040_disk_mount.sh
# CONTEXT: Arch ISO Environment - Latest Only
# PURPOSE: BTRFS Subvolume Generation, NOCOW Attributes, and FHS Mounting
# TARGET: kernel 7.1.3-arch1-1, bash 5.3.15, btrfs-progs >=6.19
# NOTES: Bleeding edge only. Assumes free-space-tree and block-group-tree are
#        defaults. Uses secure EFI mount per 2025 Arch hardening guide.
# ==============================================================================

set -euo pipefail
shopt -s inherit_errexit 2>/dev/null || true

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
# Bleeding edge: space_cache=v2 is deprecated alias for free-space-tree which is default since 5.15.
# ssd is auto-detected. compress=zstd:3 is current sweet spot for perf vs ratio.
readonly BTRFS_OPTS="rw,noatime,compress=zstd:3,discard=async"

readonly STATE_FILE="/tmp/arch_install_state.env"
if [[ -f "$STATE_FILE" ]]; then
    # shellcheck source=/dev/null
    source "$STATE_FILE"
fi

MAPPED_ROOT=""; SUCCESS=0; EFI_PART=""; ROOT_PART=""; ROOT_DISK=""
if [[ -d /sys/firmware/efi/efivars ]]; then readonly BOOT_MODE="UEFI"; else readonly BOOT_MODE="BIOS"; fi

unmount_mount_tree() {
    local mount_targets=""
    mount_targets=$(findmnt -rn -o TARGET 2>/dev/null | awk '$0=="/mnt" || index($0,"/mnt/")==1' | awk '{print length "\t" $0}' | sort -rn | cut -f2- || true)
    if [[ -n "$mount_targets" ]]; then
        while IFS= read -r mp; do [[ -n "$mp" ]] || continue; umount "$mp" 2>/dev/null || umount -R "$mp" 2>/dev/null || true; done <<< "$mount_targets"
    fi
}

cleanup() {
    local status=${1:-0}; trap - EXIT INT TERM
    if (( status != 0 )) && (( SUCCESS == 0 )); then
        if swapon --show=NAME --noheadings 2>/dev/null | grep -Fxq "$SWAPFILE_PATH"; then swapoff "$SWAPFILE_PATH" 2>/dev/null || true; fi
        unmount_mount_tree
    fi
    rm -rf "$TEMP_MNT" 2>/dev/null || true; printf '%s\n' "$C_RESET"; exit "$status"
}
trap 'cleanup "$?"' EXIT; trap 'cleanup 130' INT; trap 'cleanup 143' TERM

# Modern kernel rule for partition naming
get_partition_path() {
    local dev_path="$1" num="$2" dev_name="${dev_path##*/}"
    if [[ "$dev_name" =~ [0-9]$ ]]; then printf '%s\n' "${dev_path}p${num}"; else printf '%s\n' "${dev_path}${num}"; fi
}

is_empty_dir() {
    local dir="$1"
    if find "$dir" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null | grep -q .; then return 1; fi; return 0
}

ensure_subvolume() {
    local path="$1" nocow="${2:-0}" existed=0
    if [[ -e "$path" ]]; then
        if btrfs subvolume show "$path" >/dev/null 2>&1; then existed=1
        else echo -e "${C_RED}Critical: $path exists but not a subvolume.${C_RESET}"; exit 1; fi
    else btrfs subvolume create "$path" >/dev/null; fi
    if [[ "$nocow" == "1" ]]; then
        if (( existed == 0 )); then chattr +C "$path"
        elif is_empty_dir "$path"; then chattr +C "$path" 2>/dev/null || true; fi
    fi
}

teardown_state() {
    if swapon --show=NAME --noheadings 2>/dev/null | grep -Fxq "$SWAPFILE_PATH"; then swapoff "$SWAPFILE_PATH" 2>/dev/null || true; fi
    unmount_mount_tree; rm -rf "$TEMP_MNT" 2>/dev/null || true
}

determine_root_partition() {
    local auto_mode="$1" use_crypt=0
    if [[ "${ENCRYPT_ROOT:-}" == "1" ]]; then use_crypt=1
    elif [[ "${ENCRYPT_ROOT:-}" == "0" ]]; then use_crypt=0
    elif [[ -b "/dev/mapper/cryptroot" ]]; then use_crypt=1; else use_crypt=0; fi
    if (( use_crypt == 1 )); then
        [[ -b "/dev/mapper/cryptroot" ]] || { echo -e "${C_RED}Critical: LUKS expected but /dev/mapper/cryptroot not found.${C_RESET}"; exit 1; }
        MAPPED_ROOT="/dev/mapper/cryptroot"
        local mapped_name="${MAPPED_ROOT##*/}" backing_part=""
        backing_part=$(cryptsetup status "$mapped_name" 2>/dev/null | awk -F': *' '$1 ~ /^[[:space:]]*device$/ { print $2; exit }' || true)
        [[ -z "$backing_part" ]] && { echo -e "${C_RED}Critical: Failed to find backing device for $MAPPED_ROOT.${C_RESET}"; exit 1; }
        ROOT_PART=$(readlink -f "$backing_part")
    else
        echo -e "${C_YELLOW}>> Encryption disabled. Seeking plain root...${C_RESET}"
        if (( auto_mode == 1 )); then
            if [[ -n "${PROVISIONED_ROOT_PART:-}" && -b "$PROVISIONED_ROOT_PART" ]]; then
                ROOT_PART=$(readlink -f "$PROVISIONED_ROOT_PART"); MAPPED_ROOT="$ROOT_PART"
                echo -e "${C_CYAN}Auto-detected unencrypted BTRFS root (prev module): $ROOT_PART${C_RESET}"
            else
                local -a btrfs_parts=() part fstype
                while read -r part fstype; do [[ "$fstype" == "btrfs" ]] && btrfs_parts+=("$part"); done < <(lsblk -pnro NAME,FSTYPE 2>/dev/null || true)
                if (( ${#btrfs_parts[@]} == 1 )); then ROOT_PART=$(readlink -f "${btrfs_parts[0]}"); MAPPED_ROOT="$ROOT_PART"
                    echo -e "${C_CYAN}Auto-detected unencrypted BTRFS root: $ROOT_PART${C_RESET}"
                else echo -e "${C_RED}Critical: Cannot auto-detect unique BTRFS root. Run interactive.${C_RESET}"; exit 1; fi
            fi
        else
            echo -e "${C_CYAN}Available block devices:${C_RESET}"; lsblk -l -o NAME,SIZE,TYPE,FSTYPE,PARTTYPE,PARTLABEL
            echo ""; local raw_root; read -r -p "Enter your BTRFS root partition (e.g., nvme0n1p2): " raw_root
            ROOT_PART=$(readlink -f "/dev/${raw_root#/dev/}"); MAPPED_ROOT="$ROOT_PART"
        fi
    fi
    [[ -b "$ROOT_PART" ]] || { echo -e "${C_RED}Critical: $ROOT_PART invalid.${C_RESET}"; exit 1; }
    local root_disk_name=""; root_disk_name=$(lsblk -ndlo PKNAME "$ROOT_PART" 2>/dev/null | head -n1 || true)
    [[ -z "$root_disk_name" ]] && { echo -e "${C_RED}Critical: Failed to get parent disk for $ROOT_PART.${C_RESET}"; exit 1; }
    ROOT_DISK=$(readlink -f "/dev/${root_disk_name}"); [[ -b "$ROOT_DISK" ]] || { echo -e "${C_RED}Critical: Parent $ROOT_DISK invalid.${C_RESET}"; exit 1; }
}

validate_root_state() {
    local root_fstype=""; [[ -b "$MAPPED_ROOT" ]] || { echo -e "${C_RED}Critical: $MAPPED_ROOT not found.${C_RESET}"; exit 1; }
    root_fstype=$(lsblk -ndlo FSTYPE "$MAPPED_ROOT" 2>/dev/null | head -n1 || true)
    [[ "$root_fstype" == "btrfs" ]] || { echo -e "${C_RED}Critical: $MAPPED_ROOT is not btrfs.${C_RESET}"; exit 1; }
}

validate_efi_partition() {
    local part="$1" part_type="" parent_name="" parent_disk="" fstype="" parttype=""
    [[ -b "$part" ]] || { echo -e "${C_RED}Critical: EFI $part not found.${C_RESET}"; exit 1; }
    [[ "$part" != "$ROOT_PART" ]] || { echo -e "${C_RED}Critical: EFI cannot be root.${C_RESET}"; exit 1; }
    part_type=$(lsblk -ndlo TYPE "$part" 2>/dev/null | head -n1 || true)
    [[ "$part_type" == "part" ]] || { echo -e "${C_RED}Critical: EFI $part not a partition.${C_RESET}"; exit 1; }
    parent_name=$(lsblk -ndlo PKNAME "$part" 2>/dev/null | head -n1 || true)
    [[ -n "$parent_name" ]] || { echo -e "${C_RED}Critical: No parent for EFI $part.${C_RESET}"; exit 1; }
    parent_disk=$(readlink -f "/dev/${parent_name}")
    [[ "$parent_disk" == "$ROOT_DISK" ]] || { echo -e "${C_RED}Critical: EFI $part not on same disk as root.${C_RESET}"; exit 1; }
    fstype=$(lsblk -ndlo FSTYPE "$part" 2>/dev/null | head -n1 || true)
    parttype=$(lsblk -ndlo PARTTYPE "$part" 2>/dev/null | head -n1 || true)
    if [[ "${parttype,,}" != "$EFI_GPT_TYPE" && "${fstype,,}" != "vfat" && "${fstype,,}" != "fat32" ]]; then
        echo -e "${C_RED}Critical: $part does not look like ESP.${C_RESET}"; exit 1; fi
}

auto_detect_efi_partition() {
    local disk="$1" part type fstype parttype partlabel
    local -a guid_matches=() label_matches=() vfat_matches=() non_root_parts=()
    while read -r part type; do
        [[ "$type" == "part" ]] || continue; part=$(readlink -f "$part"); [[ "$part" == "$ROOT_PART" ]] && continue
        non_root_parts+=("$part")
        parttype=$(lsblk -ndlo PARTTYPE "$part" 2>/dev/null | head -n1 || true)
        fstype=$(lsblk -ndlo FSTYPE "$part" 2>/dev/null | head -n1 || true)
        partlabel=$(lsblk -ndlo PARTLABEL "$part" 2>/dev/null | head -n1 || true)
        [[ "${parttype,,}" == "$EFI_GPT_TYPE" ]] && guid_matches+=("$part")
        [[ "${partlabel,,}" == *efi* ]] && label_matches+=("$part")
        [[ "${fstype,,}" == "vfat" || "${fstype,,}" == "fat32" ]] && vfat_matches+=("$part")
    done < <(lsblk -pnro NAME,TYPE "$disk" 2>/dev/null)
    if (( ${#guid_matches[@]} == 1 )); then printf '%s\n' "${guid_matches[0]}"; return 0; fi; (( ${#guid_matches[@]} > 1 )) && return 1
    if (( ${#label_matches[@]} == 1 )); then printf '%s\n' "${label_matches[0]}"; return 0; fi; (( ${#label_matches[@]} > 1 )) && return 1
    if (( ${#vfat_matches[@]} == 1 )); then printf '%s\n' "${vfat_matches[0]}"; return 0; fi; (( ${#vfat_matches[@]} > 1 )) && return 1
    if (( ${#non_root_parts[@]} == 1 )); then printf '%s\n' "${non_root_parts[0]}"; return 0; fi; return 1
}

prompt_for_efi_partition() {
    local raw_efi=""; echo -e "${C_CYAN}Available partitions on ${ROOT_DISK}:${C_RESET}"
    lsblk -l -o NAME,SIZE,TYPE,FSTYPE,PARTTYPE,PARTLABEL "$ROOT_DISK"
    read -r -p "Enter your EFI partition (e.g., nvme0n1p1): " raw_efi
    EFI_PART=$(readlink -f "/dev/${raw_efi#/dev/}")
}

determine_efi_partition() {
    local auto_mode="$1" detected=""; [[ "$BOOT_MODE" == "UEFI" ]] || return 0
    if (( auto_mode == 1 )); then
        if [[ -n "${PROVISIONED_EFI_PART:-}" && -b "$PROVISIONED_EFI_PART" ]]; then
            EFI_PART=$(readlink -f "$PROVISIONED_EFI_PART"); echo -e "${C_CYAN}Auto EFI from prev module: $EFI_PART${C_RESET}"
        else detected=$(auto_detect_efi_partition "$ROOT_DISK" || true)
            if [[ -n "$detected" ]]; then EFI_PART=$(readlink -f "$detected"); echo -e "${C_CYAN}Auto EFI: $EFI_PART${C_RESET}"
            else echo -e "${C_YELLOW}>> Cannot auto-detect unique EFI. Prompting.${C_RESET}"; prompt_for_efi_partition; fi; fi
    else prompt_for_efi_partition; fi
    validate_efi_partition "$EFI_PART"
}

construct_subvolume_matrix() {
    echo -e "${C_YELLOW}>> Constructing Subvolume Matrix on Root...${C_RESET}"
    mkdir -p "$TEMP_MNT"; mount -t btrfs -o subvolid=5 "$MAPPED_ROOT" "$TEMP_MNT"
    declare -a STD_SUBVOLS=("@"
        "@home" "@snapshots" "@home_snapshots" "@var_log" "@var_cache" "@var_tmp" "@var_lib_machines" "@var_lib_portables")
    declare -a NOCOW_SUBVOLS=("@var_lib_libvirt" "@var_lib_mysql" "@var_lib_postgres" "@swap")
    local sub; for sub in "${STD_SUBVOLS[@]}"; do ensure_subvolume "${TEMP_MNT}/${sub}" 0; done
    for sub in "${NOCOW_SUBVOLS[@]}"; do ensure_subvolume "${TEMP_MNT}/${sub}" 1; done
    echo -e "${C_GREEN}>> Subvolume matrix verified.${C_RESET}"; umount "$TEMP_MNT"; rm -rf "$TEMP_MNT"
}

assemble_fhs() {
    echo -e "${C_YELLOW}>> Assembling FHS to /mnt...${C_RESET}"
    mkdir -p /mnt; mount -o "${BTRFS_OPTS},subvol=@" "$MAPPED_ROOT" /mnt
    mkdir -p /mnt/{home,.snapshots,var/log,var/cache,var/tmp,var/lib/machines,var/lib/portables,var/lib/libvirt,var/lib/mysql,var/lib/postgres,swap,boot}
    mount -o "${BTRFS_OPTS},subvol=@home" "$MAPPED_ROOT" /mnt/home
    mount -o "${BTRFS_OPTS},subvol=@snapshots" "$MAPPED_ROOT" /mnt/.snapshots
    mount -o "${BTRFS_OPTS},subvol=@var_log" "$MAPPED_ROOT" /mnt/var/log
    mount -o "${BTRFS_OPTS},subvol=@var_cache" "$MAPPED_ROOT" /mnt/var/cache
    mount -o "${BTRFS_OPTS},subvol=@var_tmp" "$MAPPED_ROOT" /mnt/var/tmp
    mount -o "${BTRFS_OPTS},subvol=@var_lib_machines" "$MAPPED_ROOT" /mnt/var/lib/machines
    mount -o "${BTRFS_OPTS},subvol=@var_lib_portables" "$MAPPED_ROOT" /mnt/var/lib/portables
    mount -o "${BTRFS_OPTS},subvol=@var_lib_libvirt" "$MAPPED_ROOT" /mnt/var/lib/libvirt
    mount -o "${BTRFS_OPTS},subvol=@var_lib_mysql" "$MAPPED_ROOT" /mnt/var/lib/mysql
    mount -o "${BTRFS_OPTS},subvol=@var_lib_postgres" "$MAPPED_ROOT" /mnt/var/lib/postgres
    mount -o "${BTRFS_OPTS},subvol=@swap" "$MAPPED_ROOT" /mnt/swap
    mkdir -p /mnt/home/.snapshots; mount -o "${BTRFS_OPTS},subvol=@home_snapshots" "$MAPPED_ROOT" /mnt/home/.snapshots
    if [[ "$BOOT_MODE" == "UEFI" ]]; then
        echo -e "${C_YELLOW}>> Mounting EFI ($EFI_PART) to /mnt/boot with secure permissions...${C_RESET}"
        mount -t vfat -o fmask=0077,dmask=0077 "$EFI_PART" /mnt/boot
    fi
}

initialize_swapfile() {
    local existing_size=""
    echo -e "${C_YELLOW}>> Ensuring 4GB Static Swapfile...${C_RESET}"
    if swapon --show=NAME --noheadings 2>/dev/null | grep -Fxq "$SWAPFILE_PATH"; then swapoff "$SWAPFILE_PATH"; fi
    if [[ -e "$SWAPFILE_PATH" && ! -f "$SWAPFILE_PATH" ]]; then echo -e "${C_RED}Critical: $SWAPFILE_PATH not a regular file.${C_RESET}"; exit 1; fi
    if [[ -f "$SWAPFILE_PATH" ]]; then
        existing_size=$(stat -Lc '%s' "$SWAPFILE_PATH" 2>/dev/null || true)
        if [[ "$existing_size" == "$SWAPFILE_SIZE_BYTES" ]]; then if swapon "$SWAPFILE_PATH" 2>/dev/null; then echo -e "${C_GREEN}>> Existing swapfile re-activated.${C_RESET}"; return 0; fi; fi
        rm -f "$SWAPFILE_PATH"
    fi
    btrfs filesystem mkswapfile --size 4G --uuid clear "$SWAPFILE_PATH"
    swapon "$SWAPFILE_PATH"
}

run_common() {
    local auto_mode="$1"; teardown_state; determine_root_partition "$auto_mode"; validate_root_state
    determine_efi_partition "$auto_mode"; construct_subvolume_matrix; assemble_fhs; initialize_swapfile
    SUCCESS=1; echo -e "\n${C_GREEN}${C_BOLD}>> Setup Complete. Primed for pacstrap.${C_RESET}"; lsblk -l -f "$ROOT_DISK" || true
}
run_auto_mode() { echo -e "${C_BOLD}=== AUTONOMOUS BTRFS MOUNT (${C_CYAN}${BOOT_MODE}${C_RESET}${C_BOLD}) ===${C_RESET}\n"; run_common 1; }
run_interactive_mode() { echo -e "${C_BOLD}=== INTERACTIVE BTRFS MOUNT (${C_CYAN}${BOOT_MODE}${C_RESET}${C_BOLD}) ===${C_RESET}\n"; run_common 0; }

if [[ "${1:-}" == "--auto" || "${1:-}" == "auto" ]]; then run_auto_mode
else read -r -p "Run AUTONOMOUS subvolume setup and mounting? [y/N]: " choice
    if [[ "${choice,,}" == "y" ]]; then run_auto_mode; else run_interactive_mode; fi
fi
exit 0
