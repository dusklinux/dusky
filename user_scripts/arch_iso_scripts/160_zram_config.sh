#!/usr/bin/env bash
# ==============================================================================
# Script Name: 009_zram_config.sh
# Description: Configures zram-generator for an Arch Linux installation.
#              Utilizes zram-generator's native math evaluation to dynamically
#              size ZRAM at boot time, making the install hardware-agnostic.
# Context:     Arch Linux Install (Chrooted Environment)
# ==============================================================================

# ------------------------------------------------------------------------------
# Strict Mode & Safety
# ------------------------------------------------------------------------------
set -euo pipefail

# ------------------------------------------------------------------------------
# Constants & Configuration
# ------------------------------------------------------------------------------
readonly CONFIG_DIR="/etc/systemd/zram-generator.conf.d"
readonly CONFIG_FILE="${CONFIG_DIR}/99-elite-zram.conf"
readonly MOUNT_POINT="/mnt/zram1"

# The formula pushed to zram-generator to be evaluated at *every boot*.
# Shape: 1:1 up to 8192 MiB -> flat at 8192 MiB until 10192 MiB -> (ram - 2000 MiB) above that.
readonly ZRAM_SIZE_EXPR='min(ram, 8192) + max(ram - 10192, 0)'
readonly COMPRESSION_ALGORITHM='zstd'
readonly FS_OPTIONS='rw,nosuid,nodev,discard,X-mount.mode=1777'

# ANSI Colors
readonly RED=$'\033[0;31m'
readonly GREEN=$'\033[0;32m'
readonly BLUE=$'\033[0;34m'
readonly YELLOW=$'\033[0;33m'
readonly NC=$'\033[0m'

# ------------------------------------------------------------------------------
# Helper Functions
# ------------------------------------------------------------------------------
log_info()    { printf '%b %s\n' "${BLUE}[INFO]${NC}" "$*"; }
log_success() { printf '%b %s\n' "${GREEN}[SUCCESS]${NC}" "$*"; }
log_warn()    { printf '%b %s\n' "${YELLOW}[WARN]${NC}" "$*"; }
log_error()   { printf '%b %s\n' "${RED}[ERROR]${NC}" "$*" >&2; }

die() {
    log_error "$@"
    exit 1
}

check_chroot_root() {
    # In a chroot, you should naturally be EUID 0. Sudo is avoided here 
    # as it may not be configured or installed in the base system yet.
    if [[ $EUID -ne 0 ]]; then
        die "This script must be run as root inside the chroot environment."
    fi
}

# ------------------------------------------------------------------------------
# Main Logic
# ------------------------------------------------------------------------------
main() {
    check_chroot_root

    # Optional sanity check: Warn if the generator isn't installed yet, 
    # but don't fail, as package installation order during chroot can vary.
    if [[ ! -f "/usr/lib/systemd/system-generators/zram-generator" ]]; then
        log_warn "zram-generator binary not found. Ensure it is installed before first boot."
    fi

    log_info "Preparing directories..."
    install -d -m 0755 -- "$CONFIG_DIR" "$MOUNT_POINT"

    log_info "Drafting ZRAM configuration atomically..."
    
    # Create a temporary file in the target directory to ensure they are on 
    # the same filesystem, allowing for a truly atomic `mv` operation.
    local tmp_config
    tmp_config="$(mktemp "${CONFIG_DIR}/.99-elite-zram.conf.tmp.XXXXXX")"
    
    # Ensure the temp file is destroyed if the script exits unexpectedly
    trap 'rm -f -- "$tmp_config"' EXIT

    # Write configuration to the temporary file
    cat >"$tmp_config" <<EOF
# Managed by Elite Arch Linux Installer.
# Dynamically calculates memory at boot via systemd zram-generator.

[zram0]
zram-size = ${ZRAM_SIZE_EXPR}
compression-algorithm = ${COMPRESSION_ALGORITHM}
swap-priority = 100
options = discard

[zram1]
zram-size = ${ZRAM_SIZE_EXPR}
fs-type = ext2
mount-point = ${MOUNT_POINT}
compression-algorithm = ${COMPRESSION_ALGORITHM}
options = ${FS_OPTIONS}
EOF

    # Set proper permissions before moving
    chmod 0644 -- "$tmp_config"
    
    # Atomically replace any existing configuration file
    mv -f -- "$tmp_config" "$CONFIG_FILE"
    
    # Disarm the trap since the file has been successfully renamed
    trap - EXIT

    log_success "ZRAM configuration generated successfully at ${CONFIG_FILE}"
    log_info "Systemd will evaluate RAM and create devices dynamically on next boot."
}

# Execute
main
