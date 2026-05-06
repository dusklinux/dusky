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
die()         { log_error "$1"; exit 1; }

readonly SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"

if [[ $EUID -ne 0 ]]; then
    printf '%b %s\n' "${YELLOW}[INFO]${NC}" "Script not run as root. Escalating privileges..."
    command -v sudo >/dev/null 2>&1 || die "sudo is required to run this script as root."
    if [[ $- == *x* ]]; then
        exec sudo -- bash -x -- "$SCRIPT_PATH" "$@"
    else
        exec sudo -- bash -- "$SCRIPT_PATH" "$@"
    fi
fi

command -v systemctl >/dev/null 2>&1 || die "systemctl is required."
command -v systemd-escape >/dev/null 2>&1 || die "systemd-escape is required."
command -v findmnt >/dev/null 2>&1 || die "findmnt is required."

readonly CONFIG_DIR="/etc/systemd/zram-generator.conf.d"
readonly CONFIG_FILE="${CONFIG_DIR}/99-elite-zram.conf"
readonly MOUNT_POINT="/mnt/zram1"

readonly ZRAM_SWAP_DEV="/dev/zram0"
readonly ZRAM_FS_DEV="/dev/zram1"

readonly ZRAM_SIZE_EXPR='min(ram, 8192) + max(ram - 10192, 0)'
readonly COMPRESSION_ALGORITHM='zstd'
readonly FS_OPTIONS='rw,nosuid,nodev,discard,X-mount.mode=1777'

readonly GENERATOR_BIN="/usr/lib/systemd/system-generators/zram-generator"
readonly SWAP_SETUP_UNIT="systemd-zram-setup@zram0.service"
readonly FS_SETUP_UNIT="systemd-zram-setup@zram1.service"
readonly SWAP_UNIT="dev-zram0.swap"
readonly MOUNT_UNIT="$(systemd-escape --path --suffix=mount "$MOUNT_POINT")"

tmp_config=""

cleanup() {
    local rc=$?
    set +e
    if [[ -n ${tmp_config:-} ]]; then
        rm -f -- "$tmp_config"
    fi
    return "$rc"
}
trap cleanup EXIT

# IMPORTANT:
# Use --mountpoint, not --target.
# --target answers "what filesystem contains this path?"
# --mountpoint answers "what is mounted exactly here?"
mount_source_exact() {
    findmnt -rn -o SOURCE --mountpoint "$MOUNT_POINT" 2>/dev/null || true
}

unit_load_state() {
    systemctl show -p LoadState --value "$1" 2>/dev/null || true
}

unit_is_loaded() {
    [[ "$(unit_load_state "$1")" == "loaded" ]]
}

assert_unit_loaded() {
    local unit=$1
    unit_is_loaded "$unit" || die "Expected generated unit is not loaded after daemon-reload: $unit"
}

# This script requires a running systemd system manager.
[[ -d /run/systemd/system ]] || die "A running systemd system manager is required."

# zram-generator does nothing in containers.
if systemd-detect-virt --quiet --container; then
    log_warn "Container detected. zram-generator does nothing inside containers; skipping."
    exit 0
fi

# zram-generator must be installed.
[[ -x "$GENERATOR_BIN" ]] || die "zram-generator is not installed at: $GENERATOR_BIN"

# Kernel cmdline can disable zram generation entirely.
if grep -Eq '(^|[[:space:]])systemd\.zram=0([[:space:]]|$)' /proc/cmdline; then
    die "Kernel command line contains systemd.zram=0, which disables zram device creation."
fi

# Refuse to reuse the mount point if something else is mounted exactly there.
current_source="$(mount_source_exact)"
if [[ -n $current_source ]]; then
    case "$current_source" in
        "$ZRAM_FS_DEV"|zram1)
            ;;
        *)
            die "$MOUNT_POINT is already mounted from $current_source; refusing to reuse it."
            ;;
    esac
fi

# Prepare directories without changing permissions on an already-mounted filesystem.
install -d -m 0755 -- "$CONFIG_DIR"

if [[ -e "$MOUNT_POINT" ]]; then
    [[ -d "$MOUNT_POINT" ]] || die "$MOUNT_POINT exists but is not a directory."
else
    install -d -m 0755 -- "$MOUNT_POINT"
fi

log_info "Directories prepared."

tmp_config="$(mktemp "${CONFIG_DIR}/.99-elite-zram.conf.tmp.XXXXXX")"

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
tmp_config=""

log_success "Configuration written to ${CONFIG_FILE}"

# Reload so the generated units are visible immediately and the config is validated.
log_info "Reloading systemd generators..."
systemctl daemon-reload

# Validate that the expected generated units now exist.
assert_unit_loaded "$SWAP_SETUP_UNIT"
assert_unit_loaded "$FS_SETUP_UNIT"
assert_unit_loaded "$SWAP_UNIT"
assert_unit_loaded "$MOUNT_UNIT"

# Do NOT attempt live teardown/recreation here.
# Upstream zram-generator documentation explicitly says reboot is the easiest
# way to apply config changes, and your failures are happening in the runtime
# reconfiguration path. For reliability, this script installs config only.
current_source="$(mount_source_exact)"
if [[ $current_source == "$ZRAM_FS_DEV" || $current_source == zram1 ]]; then
    log_info "$MOUNT_POINT is currently mounted from $current_source."
fi

if systemctl is-active --quiet "$SWAP_UNIT"; then
    log_info "$SWAP_UNIT is currently active."
fi

log_warn "Not attempting live zram reconfiguration in the current boot."
log_info "Reboot the system to apply the new zram configuration safely."

log_success "ZRAM configuration installed successfully."
