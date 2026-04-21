#!/usr/bin/env bash
# ==============================================================================
# 045_repo_bind_mount.sh
# Exposes the live USB's offline repository to the chroot environment so Phase 2
# scripts can access the local packages, avoiding arch-chroot shadow mounts.
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

# We mount the precise repo directory to avoid permission issues traversing /run
readonly SOURCE_DIR="/run/archiso/bootmnt/arch/repo"

# Safe path at the root of the new filesystem (avoids arch-chroot /run override)
readonly TARGET_DIR="/mnt/offline_repo"

log_info "Preparing to bind-mount offline repository for chroot..."

if [[ ! -d "$SOURCE_DIR" ]]; then
    log_warn "Source directory $SOURCE_DIR does not exist."
    log_warn "Assuming standard online installation. Skipping bind mount."
    exit 0
fi

if mountpoint -q "$TARGET_DIR"; then
    log_ok "Offline repository is already bind-mounted at $TARGET_DIR."
    exit 0
fi

log_info "Creating target directory: $TARGET_DIR"
mkdir -p "$TARGET_DIR"

log_info "Bind mounting $SOURCE_DIR to $TARGET_DIR"
if mount --bind "$SOURCE_DIR" "$TARGET_DIR"; then
    log_ok "Bind mount successful."
else
    log_err "Failed to bind mount the repository."
    exit 1
fi
