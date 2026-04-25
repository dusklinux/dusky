#!/usr/bin/env bash
# ==============================================================================
# 045_repo_bind_mount.sh
# RAM-Boot Resilient Offline Repository Binder
# Handles: copytoram evasion, Ventoy block mapping, and pacstrap sandboxing.
# ==============================================================================

set -euo pipefail

readonly G=$'\e[32m' Y=$'\e[33m' R=$'\e[31m' B=$'\e[34m' RS=$'\e[0m'

log_info()  { printf "%s[INFO]%s  %s\n" "$B" "$RS" "$1"; }
log_ok()    { printf "%s[OK]%s    %s\n" "$G" "$RS" "$1"; }
log_warn()  { printf "%s[WARN]%s  %s\n" "$Y" "$RS" "$1"; }
log_err()   { printf "%s[ERR]%s   %s\n" "$R" "$RS" "$1" >&2; }

if (( EUID != 0 )); then
    log_err "This script must be run as root."
    exit 1
fi

readonly ISO_MNT="/run/archiso/bootmnt"
readonly SOURCE_DIR="${ISO_MNT}/arch/repo"

log_info "Preparing to bind-mount offline repository..."

# --- 1. VFS RECOVERY PROTOCOL (copytoram / Ventoy evasion) ---
# Low-RAM systems (<= 4GB) won't trigger copytoram, so the directory will naturally exist.
# High-RAM systems unmount the ISO, so we dynamically recover the block device.
if [[ ! -d "$SOURCE_DIR" ]]; then
    log_warn "Source directory missing. ISO was likely unmounted via copytoram."
    log_info "Hunting for iso9660 block device to remount..."
    
    # Prevent pipefail from crashing the script if blkid returns empty
    ISO_DEV=$(blkid -t TYPE=iso9660 -o device | head -n 1 || true)
    
    # Fallback to Ventoy's device mapper if blkid is blinded by abstractions
    if [[ -z "$ISO_DEV" && -b "/dev/mapper/ventoy" ]]; then
        log_info "Native blkid missed, but Ventoy mapper detected."
        ISO_DEV="/dev/mapper/ventoy"
    fi
    
    if [[ -n "$ISO_DEV" ]]; then
        log_info "Remounting ISO block device ($ISO_DEV) to $ISO_MNT..."
        mkdir -p "$ISO_MNT"
        if ! mountpoint -q "$ISO_MNT"; then
            mount -o ro "$ISO_DEV" "$ISO_MNT" || {
                log_err "Failed to mount $ISO_DEV. Offline installation may fail."
                exit 1
            }
        fi
    else
        log_warn "Could not locate iso9660 block device or Ventoy mapper."
        log_warn "Assuming standard online installation. Skipping bind mount."
        exit 0
    fi
fi

# Final sanity check to guarantee payload availability
if [[ ! -d "$SOURCE_DIR" ]]; then
    log_err "Critical failure: $SOURCE_DIR still does not exist after recovery."
    exit 1
fi

# --- 2. ESTABLISH UNMASKABLE BIND MOUNTS ---
# pacstrap's chroot overwrites /run, /sys, /dev, and /proc. We must route the 
# repo through a custom root-level directory (/offline_repo) to survive the sandbox.

# 2a. Live Environment Target (Triggers 051_pacman_repo_switch.sh logic)
readonly TARGET_LIVE="/offline_repo"
if ! mountpoint -q "$TARGET_LIVE"; then
    log_info "Creating Live ISO target directory: $TARGET_LIVE"
    mkdir -p "$TARGET_LIVE"
    mount --bind "$SOURCE_DIR" "$TARGET_LIVE"
    log_ok "Live ISO unmaskable bind mount successful."
else
    log_ok "Live ISO target already mounted at $TARGET_LIVE."
fi

# 2b. Chroot Target (Ensures the repo exists at /offline_repo INSIDE the chroot)
readonly TARGET_CHROOT="/mnt/offline_repo"
if ! mountpoint -q "$TARGET_CHROOT"; then
    log_info "Creating Chroot target directory: $TARGET_CHROOT"
    mkdir -p "$TARGET_CHROOT"
    mount --bind "$TARGET_LIVE" "$TARGET_CHROOT"
    log_ok "Chroot unmaskable bind mount successful."
else
    log_ok "Chroot target already mounted at $TARGET_CHROOT."
fi
