#!/usr/bin/env bash
# ==============================================================================
# Script Name: 160_zram_config.sh
# Description: Configures base zram-generator for an Arch Linux installation.
#              Aligned with user-space script 205. Primes the system 
#              with ZSTD swap on first boot.
# Context:     Arch Linux Install (Chrooted Environment)
# Note:        Zero filesystem journaling is involved here; zram0 is pure swap.
#              Secondary ephemeral storage (/mnt/zram1) is delegated to script 206
#              which applies native high-performance Tmpfs (2x RAM ceiling, zero double-buffering).
# ==============================================================================

set -euo pipefail

# ------------------------------------------------------------------------------
# Constants & Configuration
# ------------------------------------------------------------------------------
readonly CONFIG_DIR="/etc/systemd/zram-generator.conf.d"
readonly CONFIG_FILE="${CONFIG_DIR}/99-zram0.conf"
readonly COMPRESSION_ALGORITHM="zstd(level=2)"
readonly SWAP_PRIORITY=32767

# ------------------------------------------------------------------------------
# Memory Tier Detection (Matches 205_zram_configuration.sh & Fable 5.1 Max)
# ------------------------------------------------------------------------------
declare -i RAM_KB=0
if [[ $(< /proc/meminfo) =~ MemTotal:[[:space:]]+([0-9]+) ]]; then
    RAM_KB=$(( BASH_REMATCH[1] ))
else
    RAM_KB=$(awk '/^MemTotal:/{print $2}' /proc/meminfo 2>/dev/null || echo 0)
fi

declare -i RAM_MB=$(( RAM_KB / 1024 ))
declare -i RAM_GB=$(( (RAM_MB + 512) / 1024 ))

ZRAM_SIZE_EXPR="ram"
ZRAM_RESIDENT_LIMIT_EXPR="ram / 2"
TIER_DESC=""

if (( RAM_MB <= 8704 )); then
    ZRAM_SIZE_EXPR="ram"
    ZRAM_RESIDENT_LIMIT_EXPR="ram / 2"
    TIER_DESC="<= 8GB RAM (${RAM_GB}GB detected) -> Size: 100% (1.0x), Resident Cap: 50% (0.5x)"
elif (( RAM_MB < 31744 )); then
    ZRAM_SIZE_EXPR="ram"
    ZRAM_RESIDENT_LIMIT_EXPR="ram / 2"
    TIER_DESC="8GB - 32GB RAM (${RAM_GB}GB detected) -> Size: 100% (1.0x), Resident Cap: 50% (0.5x)"
else
    ZRAM_SIZE_EXPR="ram / 2"
    ZRAM_RESIDENT_LIMIT_EXPR="0"
    TIER_DESC=">= 32GB RAM (${RAM_GB}GB detected) -> Size: 50% (0.5x), Resident Cap: Unlimited (0)"
fi

readonly ZRAM_SIZE_EXPR
readonly ZRAM_RESIDENT_LIMIT_EXPR

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
    if [[ $EUID -ne 0 ]]; then
        die "This script must be run as root inside the chroot environment."
    fi
}

# ------------------------------------------------------------------------------
# Main Logic
# ------------------------------------------------------------------------------
main() {
    check_chroot_root

    log_info "Detected Target Memory Tier: ${TIER_DESC}"

    if [[ ! -f "/usr/lib/systemd/system-generators/zram-generator" ]]; then
        log_warn "zram-generator binary not found. Ensure package 'zram-generator' is installed."
    fi

    # --- 1. Persistent ZSWAP Suppression ---
    # Prevents double-compression overhead with ZRAM starting on first boot
    log_info "Configuring declarative ZSWAP suppression (/etc/tmpfiles.d/00-disable-zswap.conf)..."
    install -d -m 0755 /etc/tmpfiles.d
    cat > /etc/tmpfiles.d/00-disable-zswap.conf <<'EOF'
# Disable zswap to prevent redundant double-compression with ZRAM
w-! /sys/module/zswap/parameters/enabled - - - - 0
EOF
    log_success "ZSWAP disablement staged for first boot."

    # --- 2. Configure zram0 Swap via systemd-zram-generator ---
    log_info "Preparing configuration directory: ${CONFIG_DIR}..."
    install -d -m 0755 -- "$CONFIG_DIR"

    # Clean up any legacy config names
    rm -f "${CONFIG_DIR}/99-elite-zram.conf" \
          "${CONFIG_DIR}/99-elite-zram0.conf" \
          "${CONFIG_DIR}/99-elite-zram1.conf" \
          "${CONFIG_DIR}/99-memtune.conf"

    log_info "Drafting initial ZRAM swap configuration atomically..."
    
    local tmp_config
    tmp_config="$(umask 077 && mktemp)"
    trap 'rm -f -- "$tmp_config"' EXIT

    # Note: [zram1] block device is intentionally omitted to prevent kernel page-cache
    # double-buffering. Ephemeral /mnt/zram1 storage is configured post-install
    # via user-space script 206 as native high-performance Tmpfs (2x RAM ceiling).
    cat >"$tmp_config" <<EOF
# Managed by 160_zram_config.sh (Arch Linux ISO Installer)
# Base topology primed for first boot (aligned with script 205).

[zram0]
zram-size = ${ZRAM_SIZE_EXPR}
zram-resident-limit = ${ZRAM_RESIDENT_LIMIT_EXPR}
compression-algorithm = ${COMPRESSION_ALGORITHM}
swap-priority = ${SWAP_PRIORITY}
options = discard
EOF

    install -Dm0644 "$tmp_config" "$CONFIG_FILE"
    rm -f -- "$tmp_config"
    trap - EXIT

    log_success "Base ZRAM swap architecture generated successfully at ${CONFIG_FILE} (${ZRAM_SIZE_EXPR}, Priority ${SWAP_PRIORITY})"
}

main
