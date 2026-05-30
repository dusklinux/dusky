#!/usr/bin/env bash
# ==============================================================================
# Script: 151_systemd_bootloader.sh
# Description: Automated, dynamically-mapped systemd-boot configuration.
# Architecture: UEFI -> systemd-boot -> [LUKS2 Auto-Detect] -> [FSTYPE Auto-Detect]
# Standard: systemd v260.1+ (UAPI.1 Boot Loader Specification)
# ==============================================================================

set -euo pipefail
export LC_ALL=C

# --- Visuals ---
readonly C_BOLD=$'\033[1m'
readonly C_RESET=$'\033[0m'
readonly C_BLUE=$'\033[1;34m'
readonly C_GREEN=$'\033[1;32m'
readonly C_YELLOW=$'\033[1;33m'
readonly C_RED=$'\033[1;31m'

log_info()    { printf "${C_BLUE}[INFO]${C_RESET} %s\n" "$*"; }
log_success() { printf "${C_GREEN}[OK]${C_RESET} %s\n" "$*"; }
log_warn()    { printf "${C_YELLOW}[WARN]${C_RESET} %s\n" "$*" >&2; }
log_error()   { printf "${C_RED}[ERROR]${C_RESET} %s\n" "$*" >&2; }

cleanup() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        log_error "Script failed at line ${BASH_LINENO[0]} (Exit Code: $exit_code)."
    fi
}
trap cleanup EXIT

# ==============================================================================
# 1. Environment & Pre-Flight Checks
# ==============================================================================

if [[ $EUID -ne 0 ]]; then
    log_error "This script must be run as root within the arch-chroot."
    exit 1
fi

if [[ ! -d /sys/firmware/efi/efivars ]]; then
    log_error "No UEFI variables found. systemd-boot strictly requires UEFI mode."
    exit 1
fi

ESP_MNT="/boot"
if ! mountpoint -q "$ESP_MNT"; then
    log_error "$ESP_MNT is NOT a mountpoint. Ensure your FAT32 ESP is mounted."
    exit 1
fi

ESP_FSTYPE=$(findmnt -n -e -o FSTYPE "$ESP_MNT" | head -n1 2>/dev/null || true)
if [[ ! "$ESP_FSTYPE" =~ ^(vfat|fat32|msdos)$ ]]; then
    log_error "$ESP_MNT is formatted as $ESP_FSTYPE, but systemd-boot requires FAT32."
    exit 1
fi

log_info "Ensuring necessary bootloader packages..."
pacman -S --needed --noconfirm efibootmgr gawk >/dev/null

# ==============================================================================
# 2. Dynamic Topology Traversal (Auto-Detects LUKS & FSTYPE)
# ==============================================================================

log_info "Analyzing filesystem topology..."

# Safe extraction to prevent chroot multi-bind parsing failures.
# The '-v' (--nofsroot) flag natively strips subvolume tags (e.g. [/@]),
# making older bash string-slicing workarounds obsolete.
ROOT_BLK_DEV=$(findmnt -n -v -e -o SOURCE -T / | head -n1)
ROOT_UUID=$(findmnt -n -e -o UUID -T / | head -n1 || true)
ROOT_FSTYPE=$(findmnt -n -e -o FSTYPE -T / | head -n1 2>/dev/null || true)

[[ -z "$ROOT_BLK_DEV" ]] && { log_error "Could not resolve root block device."; exit 1; }

if [[ -z "$ROOT_UUID" || "$ROOT_UUID" == "-" ]]; then
    ROOT_UUID=$(blkid -s UUID -o value "$ROOT_BLK_DEV" | head -n1)
fi
[[ -z "$ROOT_UUID" ]] && { log_error "Could not resolve root filesystem UUID."; exit 1; }
[[ -z "$ROOT_FSTYPE" || "$ROOT_FSTYPE" == "-" ]] && ROOT_FSTYPE="btrfs"

ROOT_OPTS=$(findmnt -n -e -o OPTIONS -T / | head -n1 || true)
ROOT_SUBVOL=""
if [[ "$ROOT_OPTS" =~ subvol=([^,]+) ]]; then
    ROOT_SUBVOL="${BASH_REMATCH[1]}"
fi

# Extract initramfs hooks to adjust kernel cmdline logically
HOOKS_STR=$(env -i bash -c '
    source /etc/mkinitcpio.conf >/dev/null 2>&1 || true
    shopt -s nullglob
    for conf in /etc/mkinitcpio.conf.d/*.conf; do
        source "$conf" >/dev/null 2>&1 || true
    done
    echo "${HOOKS[*]:-}"
')

# Base arguments inherited from original configurations
CMDLINE_BASE="rw loglevel=3 zswap.enabled=0 rootfstype=${ROOT_FSTYPE}"

# BTRFS lacks a boot-time fsck. Skip it to prevent harmless but annoying warnings.
if [[ "$ROOT_FSTYPE" == "btrfs" ]]; then
    CMDLINE_BASE="${CMDLINE_BASE} fsck.mode=skip"
fi

# Inverse trace block device dependencies to search for a crypt layer
CRYPT_DEV=$(lsblk -nrspo PATH,TYPE -s -- "$ROOT_BLK_DEV" 2>/dev/null | awk '$2 == "crypt" { print $1; exit }')

if [[ -n "$CRYPT_DEV" ]]; then
    log_info "LUKS2 Encryption detected on root device."
    MAPPER_NAME="${CRYPT_DEV##*/}"
    BACKING_DEV=$(cryptsetup status "$MAPPER_NAME" | awk '/^[[:space:]]*device:/ { print $2; exit }')
    [[ -z "$BACKING_DEV" ]] && { log_error "Could not determine backing device for $MAPPER_NAME."; exit 1; }
    
    LUKS_UUID=$(blkid -s UUID -o value "$BACKING_DEV" | head -n1)
    [[ -z "$LUKS_UUID" ]] && { log_error "Could not determine LUKS UUID for $BACKING_DEV."; exit 1; }

    if [[ " $HOOKS_STR " == *" sd-encrypt "* ]]; then
        log_info "Systemd encryption hook (sd-encrypt) detected."
        CMDLINE_BASE="rd.luks.name=${LUKS_UUID}=${MAPPER_NAME} rd.luks.options=discard root=UUID=${ROOT_UUID} ${CMDLINE_BASE}"
    elif [[ " $HOOKS_STR " == *" encrypt "* ]]; then
        log_info "Legacy encryption hook (encrypt) detected."
        CMDLINE_BASE="cryptdevice=UUID=${LUKS_UUID}:${MAPPER_NAME}:allow-discards root=/dev/mapper/${MAPPER_NAME} ${CMDLINE_BASE}"
    else
        log_error "LUKS detected, but neither 'sd-encrypt' nor 'encrypt' hook found in mkinitcpio configs."
        exit 1
    fi
else
    log_info "No LUKS layer detected. Configuring for plain ${ROOT_FSTYPE^^}."
    CMDLINE_BASE="root=UUID=${ROOT_UUID} ${CMDLINE_BASE}"
fi

if [[ -n "$ROOT_SUBVOL" ]]; then
    CMDLINE_BASE="${CMDLINE_BASE} rootflags=subvol=${ROOT_SUBVOL}"
fi

# Dynamically apply Plymouth arguments ONLY if the hook is installed
PLYMOUTH_ARGS=""
if [[ " $HOOKS_STR " == *" plymouth "* || " $HOOKS_STR " == *" sd-plymouth "* ]]; then
    log_info "Plymouth integration detected. Appending splash arguments."
    PLYMOUTH_ARGS="quiet splash rd.udev.log_level=3 vt.global_cursor_default=0 nowatchdog"
fi

log_success "Topology mapped securely. Base kernel command line established."

# ==============================================================================
# 3. Systemd-Boot Installation (v260.1+ Standard)
# ==============================================================================

log_info "Deploying systemd-boot to $ESP_MNT..."

# Use --variables=yes to ensure NVRAM writes inside chroot.
# Use --efi-boot-option-description-with-device=yes (Systemd 260+) for hardware context in UEFI menu.
if bootctl is-installed --esp-path="$ESP_MNT" >/dev/null 2>&1; then
    log_info "Existing systemd-boot detected. Performing update..."
    bootctl update --esp-path="$ESP_MNT" --variables=yes --efi-boot-option-description-with-device=yes
else
    log_info "Performing fresh systemd-boot installation..."
    if ! bootctl install --esp-path="$ESP_MNT" --variables=yes --efi-boot-option-description-with-device=yes; then
        log_warn "Installation returned non-zero (common on restricted firmware). Verifying deployment..."
        if ! bootctl is-installed --esp-path="$ESP_MNT" >/dev/null 2>&1; then
             log_error "bootctl installation failed completely."
             exit 1
        fi
    fi
fi

log_success "systemd-boot binaries deployed. Early-boot entropy seeded automatically."

LOADER_CONF="$ESP_MNT/loader/loader.conf"
cat > "$LOADER_CONF" <<EOF
default  @saved
timeout  2
console-mode max
editor   no
EOF

# ==============================================================================
# 4. Deferred Hook Bridging & BLS Generation
# ==============================================================================

log_info "Staging kernels for deferred mkinitcpio generation..."

declare -a KERNELS=()
for kdir in /usr/lib/modules/*; do
    if [[ -f "$kdir/pkgbase" && -f "$kdir/vmlinuz" ]]; then
        KNAME="$(<"$kdir/pkgbase")"
        KERNELS+=("$KNAME")
        
        log_info "Copying kernel binary for '$KNAME' to $ESP_MNT/vmlinuz-$KNAME..."
        cp -p "$kdir/vmlinuz" "$ESP_MNT/vmlinuz-$KNAME"
    fi
done

if (( ${#KERNELS[@]} == 0 )); then
    log_error "No valid kernel payloads found in /usr/lib/modules. pacstrap failure?"
    exit 1
fi

shopt -s nullglob
UCODES=("$ESP_MNT"/*-ucode.img)
shopt -u nullglob

mkdir -p "$ESP_MNT/loader/entries"

for KNAME in "${KERNELS[@]}"; do
    ENTRY_FILE="$ESP_MNT/loader/entries/arch-${KNAME}.conf"
    FALLBACK_FILE="$ESP_MNT/loader/entries/arch-${KNAME}-fallback.conf"
    
    log_info "Generating BLS Type #1 entries for: Arch Linux ($KNAME)"

    # Formulate Primary Options Line
    PRIMARY_OPTS="$CMDLINE_BASE"
    if [[ -n "$PLYMOUTH_ARGS" ]]; then
        PRIMARY_OPTS="${PRIMARY_OPTS} ${PLYMOUTH_ARGS}"
    fi

    # --- Primary Entry ---
    {
        printf "title   Arch Linux (%s)\n" "$KNAME"
        printf "linux   /vmlinuz-%s\n" "$KNAME"
        
        for ucode in "${UCODES[@]}"; do
            printf "initrd  /%s\n" "${ucode##*/}"
        done
        
        printf "initrd  /initramfs-%s.img\n" "$KNAME"
        printf "options %s\n" "$PRIMARY_OPTS"
    } > "$ENTRY_FILE"

    # --- Fallback Entry (Excludes Plymouth to ensure full debug verbosity) ---
    {
        printf "title   Arch Linux (%s - Fallback Recovery)\n" "$KNAME"
        printf "linux   /vmlinuz-%s\n" "$KNAME"
        
        for ucode in "${UCODES[@]}"; do
            printf "initrd  /%s\n" "${ucode##*/}"
        done
        
        printf "initrd  /initramfs-%s-fallback.img\n" "$KNAME"
        printf "options %s\n" "$CMDLINE_BASE"
    } > "$FALLBACK_FILE"
done

# ==============================================================================
# 5. Lifecycle Hooks
# ==============================================================================

log_info "Enabling systemd-boot-update.service (Auto-updates bootloader)..."
systemctl enable systemd-boot-update.service >/dev/null 2>&1 || true

log_success "Systemd-Boot orchestration complete. Kernels staged for mkinitcpio."
