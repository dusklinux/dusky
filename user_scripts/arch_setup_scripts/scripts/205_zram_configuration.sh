#!/usr/bin/env bash
# Zram Configuration
# -----------------------------------------------------------------------------
# Elite Arch Linux ZRAM Configurator
# Context: Hyprland / UWSM Environment
# -----------------------------------------------------------------------------

set -euo pipefail

GREEN=$'\033[32m'
BLUE=$'\033[34m'
YELLOW=$'\033[33m'
RED=$'\033[31m'
NC=$'\033[0m'

log_info()    { printf '%b %s\n' "${BLUE}[INFO]${NC}" "$1"; }
log_success() { printf '%b %s\n' "${GREEN}[SUCCESS]${NC}" "$1"; }
log_warn()    { printf '%b %s\n' "${YELLOW}[WARN]${NC}" "$1"; }
log_error()   { printf '%b %s\n' "${RED}[ERROR]${NC}" "$1" >&2; }

readonly SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"

if [[ $EUID -ne 0 ]]; then
    printf '%b %s\n' "${YELLOW}[INFO]${NC}" "Script not run as root. Escalating privileges..."
    if [[ $- == *x* ]]; then
        exec sudo -- bash -x -- "$SCRIPT_PATH" "$@"
    else
        exec sudo -- bash -- "$SCRIPT_PATH" "$@"
    fi
fi

readonly CONFIG_DIR="/etc/systemd/zram-generator.conf.d"
readonly CONFIG_FILE="${CONFIG_DIR}/99-elite-zram.conf"
readonly MOUNT_POINT="/mnt/zram1"

readonly ZRAM_SIZE_EXPR='min(ram, 8192) + max(ram - 10192, 0)'
readonly COMPRESSION_ALGORITHM='zstd'
readonly FS_OPTIONS='rw,nosuid,nodev,discard,X-mount.mode=1777'

readonly GENERATOR_BIN="/usr/lib/systemd/system-generators/zram-generator"
readonly SWAP_SETUP_UNIT="systemd-zram-setup@zram0.service"
readonly FS_SETUP_UNIT="systemd-zram-setup@zram1.service"
readonly SWAP_UNIT="dev-zram0.swap"
readonly MOUNT_UNIT="$(systemd-escape --path --suffix=mount "$MOUNT_POINT")"

# zram-generator(8): generator does nothing in containers.
if systemd-detect-virt --quiet --container; then
    log_warn "Container detected. zram-generator does nothing inside containers; skipping."
    exit 0
fi

# zram-generator must be installed.
if [[ ! -x "$GENERATOR_BIN" ]]; then
    log_error "zram-generator is not installed at: $GENERATOR_BIN"
    exit 1
fi

# zram-generator.conf(5): kernel cmdline systemd.zram=0 overrides config and disables creation.
if grep -Eq '(^|[[:space:]])systemd\.zram=0([[:space:]]|$)' /proc/cmdline; then
    log_error "Kernel command line contains systemd.zram=0, which disables zram device creation."
    exit 1
fi

# Refuse to reuse the mount point if something else is already mounted there.
current_source="$(findmnt -rn -o SOURCE --target "$MOUNT_POINT" 2>/dev/null || true)"
if [[ -n $current_source ]]; then
    case "$current_source" in
        /dev/zram1|zram1)
            ;;
        *)
            log_error "$MOUNT_POINT is already mounted from $current_source; refusing to reuse it."
            exit 1
            ;;
    esac
fi

install -d -m 0755 -- "$CONFIG_DIR" "$MOUNT_POINT"
log_info "Directories prepared."

tmp_config="$(mktemp "${CONFIG_DIR}/.99-elite-zram.conf.tmp.XXXXXX")"
cleanup() {
    [[ -n ${tmp_config:-} ]] && rm -f -- "$tmp_config"
}
trap cleanup EXIT

cat >"$tmp_config" <<EOF
# Managed by Elite Arch Linux ZRAM Configurator.
# Manual edits to this file may be overwritten.

[zram0]
# Intentionally the same size policy as zram1.
# Shape:
#   - 1:1 up to 8192 MiB
#   - flat at 8192 MiB until 10192 MiB
#   - then (ram - 2000 MiB) above that point
zram-size = ${ZRAM_SIZE_EXPR}
compression-algorithm = ${COMPRESSION_ALGORITHM}
swap-priority = 100
options = discard

[zram1]
# Intentionally the same size policy as zram0.
zram-size = ${ZRAM_SIZE_EXPR}
fs-type = ext2
mount-point = ${MOUNT_POINT}
compression-algorithm = ${COMPRESSION_ALGORITHM}
options = ${FS_OPTIONS}
EOF

chmod 0644 -- "$tmp_config"
mv -f -- "$tmp_config" "$CONFIG_FILE"
log_success "Configuration written to ${CONFIG_FILE}"

log_info "Reloading systemd generators and recreating zram devices..."
systemctl daemon-reload
systemctl restart "$SWAP_SETUP_UNIT" "$FS_SETUP_UNIT"

# Ensure the consumer units are active after recreation.
systemctl start "$SWAP_UNIT" "$MOUNT_UNIT"

if ! systemctl is-active --quiet "$SWAP_UNIT"; then
    log_error "$SWAP_UNIT is not active after applying the configuration."
    exit 1
fi

if ! systemctl is-active --quiet "$MOUNT_UNIT"; then
    log_error "$MOUNT_UNIT is not active after applying the configuration."
    exit 1
fi

# Enforce the intended final mode on the mounted filesystem root right now.
chmod 1777 -- "$MOUNT_POINT"

log_success "ZRAM configuration complete and active."
