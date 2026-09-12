#!/usr/bin/env bash
# ==============================================================================
# 205_zram_configuration.sh
# Scope: High-Performance ZRAM Swap Configurator (Kernel 7.2+, systemd 261+)
# Strategy: Lowest RAM usage without compromising performance via dynamic tiering.
# ==============================================================================

set -euo pipefail

readonly SCRIPT_NAME="${0##*/}"
ORIG_ARGS=("$@")
readonly SELF_PATH="$(realpath -e -- "${BASH_SOURCE[0]}")"

# --- 1. Privilege Escalation (Executed First) ---
if [[ ${EUID} -ne 0 ]]; then
    command -v sudo >/dev/null 2>&1 || { echo "Error: root privileges and sudo required." >&2; exit 1; }
    exec sudo -- /usr/bin/bash "$SELF_PATH" "${ORIG_ARGS[@]}"
fi

# --- 2. ANSI Formatting ---
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    C_RESET=$'\033[0m'
    C_GREEN=$'\033[1;32m'
    C_BLUE=$'\033[1;34m'
    C_RED=$'\033[1;31m'
    C_YELLOW=$'\033[1;33m'
    C_BOLD=$'\033[1m'
else
    C_RESET='' C_GREEN='' C_BLUE='' C_RED='' C_YELLOW='' C_BOLD=''
fi

log_info()    { printf '%s[INFO]%s %s\n'  "$C_BLUE"   "$C_RESET" "$1"; }
log_success() { printf '%s[OK]%s %s\n'    "$C_GREEN"  "$C_RESET" "$1"; }
log_warn()    { printf '%s[WARN]%s %s\n'  "$C_YELLOW" "$C_RESET" "$1"; }
log_error()   { printf '%s[ERROR]%s %s\n' "$C_RED"    "$C_RESET" "$1" >&2; }
die()         { log_error "$1"; exit "${2:-1}"; }

print_help() {
    cat <<EOF
${C_BOLD}Usage:${C_RESET} ${SCRIPT_NAME} [OPTIONS]

Configure high-efficiency ZRAM swap for Arch Linux (Linux 7.2+, systemd 261+).

Options:
  --size, -s <expr>           ZRAM size expression (auto-detected if omitted)
                              • < 32GB class  -> "ram * 1.5" (150% RAM - Expands tight memory)
                              • >= 32GB class -> "ram / 2"   (50% RAM - Massive headroom)
  --resident-limit, -r <expr> Resident memory limit expression (default: 0 / unlimited)
  --priority, -p <prio>       Swap priority (default: 32767 - Maximum priority over disk)
  --algorithm, -a <algo>      Compression algorithm (default: "zstd(level=2)")
  --help, -h                  Show this help menu
EOF
}

usage_error() { log_error "$1"; print_help >&2; exit 2; }

# --- 3. Dynamic Hardware & Memory Tier Detection ---
declare -i RAM_KB=0
if [[ $(< /proc/meminfo) =~ MemTotal:[[:space:]]+([0-9]+) ]]; then
    RAM_KB=$(( BASH_REMATCH[1] ))
else
    RAM_KB=$(awk '/^MemTotal:/{print $2}' /proc/meminfo 2>/dev/null || echo 0)
fi

declare -i RAM_MB=$(( RAM_KB / 1024 ))
declare -i RAM_GB=$(( (RAM_MB + 512) / 1024 ))

AUTO_SIZE_EXPR="ram"
AUTO_LIMIT_EXPR="0"
TIER_DESC=""

# Unified Tier Demarcation (28 GiB / 29,360,128 KiB accounts for 32GB systems with iGPU reservations)
if (( RAM_KB < 29360128 )); then
    AUTO_SIZE_EXPR="ram * 1.5"
    AUTO_LIMIT_EXPR="0"
    TIER_DESC="Standard (<32GB class, ${RAM_GB}GB detected) -> Size: 150% (1.5x RAM), Resident Cap: unlimited (0)"
else
    AUTO_SIZE_EXPR="ram / 2"
    AUTO_LIMIT_EXPR="0"
    TIER_DESC="High-Capacity (>=32GB class, ${RAM_GB}GB detected) -> Size: 50% (0.5x RAM), Resident Cap: unlimited (0)"
fi

ZRAM_SIZE_EXPR=""
ZRAM_RESIDENT_LIMIT_EXPR=""
SWAP_PRIORITY="32767"
COMPRESSION_ALGORITHM="zstd(level=2)"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --size|-s)
            [[ $# -ge 2 ]] || usage_error "Missing value for $1"
            ZRAM_SIZE_EXPR="$2"
            shift 2
            ;;
        --resident-limit|-r)
            [[ $# -ge 2 ]] || usage_error "Missing value for $1"
            ZRAM_RESIDENT_LIMIT_EXPR="$2"
            shift 2
            ;;
        --priority|-p)
            [[ $# -ge 2 ]] || usage_error "Missing value for $1"
            SWAP_PRIORITY="$2"
            shift 2
            ;;
        --algorithm|-a)
            [[ $# -ge 2 ]] || usage_error "Missing value for $1"
            COMPRESSION_ALGORITHM="$2"
            shift 2
            ;;
        --help|-h) print_help; exit 0 ;;
        *) usage_error "Unknown argument: $1" ;;
    esac
done

if [[ -z "$ZRAM_SIZE_EXPR" ]]; then
    ZRAM_SIZE_EXPR="$AUTO_SIZE_EXPR"
    log_info "Auto-detected Memory Tier: ${C_BOLD}${TIER_DESC}${C_RESET}"
else
    log_info "Manual ZRAM Size Override: ${C_BOLD}${ZRAM_SIZE_EXPR}${C_RESET}"
fi

if [[ -z "$ZRAM_RESIDENT_LIMIT_EXPR" ]]; then
    ZRAM_RESIDENT_LIMIT_EXPR="$AUTO_LIMIT_EXPR"
    log_info "Auto-configured Resident Limit: ${C_BOLD}${ZRAM_RESIDENT_LIMIT_EXPR}${C_RESET}"
else
    log_info "Manual Resident Limit Override: ${C_BOLD}${ZRAM_RESIDENT_LIMIT_EXPR}${C_RESET}"
fi

# Container environment guard
if systemd-detect-virt --quiet --container; then
    log_warn "Container detected. Skipping ZRAM device provisioning."
    exit 0
fi

# --- 5. Dependency Validation & Safe Pacman Bootstrap ---
for cmd in systemctl grep sed install pacman; do
    command -v "$cmd" >/dev/null 2>&1 || die "Required command '$cmd' is missing."
done

readonly GENERATOR_BIN="/usr/lib/systemd/system-generators/zram-generator"
if [[ ! -x "$GENERATOR_BIN" ]]; then
    log_info "zram-generator is missing. Bootstrapping via pacman..."
    declare -i wait_seconds=0
    while [[ -f /var/lib/pacman/db.lck ]]; do
        if ! pgrep -x pacman >/dev/null 2>&1; then
            log_warn "Stale lock detected at /var/lib/pacman/db.lck (no active pacman process)."
        fi
        log_warn "Pacman is currently locked. Waiting 2 seconds... ($wait_seconds/16s)"
        sleep 2
        wait_seconds+=2
        if (( wait_seconds >= 16 )); then
            die "Pacman database lock held for more than 16 seconds. Please verify pacman state."
        fi
    done
    pacman -S --needed --noconfirm zram-generator || die "Installation failed. Please run 'pacman -Syu' first."
    log_success "zram-generator successfully installed."
fi

if grep -Eq '(^|[[:space:]])systemd\.zram=0([[:space:]]|$)' /proc/cmdline; then
    die "FATAL: Kernel cmdline explicitly disables zram device creation via systemd.zram=0."
fi

# --- 6. Non-Destructive ZSWAP Suppression ---
log_info "Verifying ZSWAP state..."
readonly ZSWAP_PARAM="/sys/module/zswap/parameters/enabled"
if [[ -w "$ZSWAP_PARAM" ]]; then
    current_zswap=$(<"$ZSWAP_PARAM")
    if [[ "$current_zswap" == "Y" || "$current_zswap" == "1" ]]; then
        log_info "Live memory: Disabling zswap in the running kernel..."
        echo 0 > "$ZSWAP_PARAM" || log_warn "Failed to live-disable zswap."
    else
        log_success "Live memory: ZSWAP is cleanly disabled."
    fi
fi

# Declarative persistence via tmpfiles (safe across UKIs, GRUB, systemd-boot, Limine)
install -d -m 0755 /etc/tmpfiles.d
cat > /etc/tmpfiles.d/00-disable-zswap.conf <<'EOF'
# Disable zswap to prevent redundant double-compression with ZRAM
w- /sys/module/zswap/parameters/enabled - - - - 0
EOF
log_success "Persistence: Created /etc/tmpfiles.d/00-disable-zswap.conf (bootloader-agnostic)."

# Informational non-destructive bootloader inspection
readonly CMDLINE_FILE="/etc/kernel/cmdline"
if [[ -f "$CMDLINE_FILE" ]]; then
    if grep -q -E '(^|[[:space:]])zswap\.enabled=0([[:space:]]|$)' "$CMDLINE_FILE"; then
        log_success "Bootloader cmdline: zswap.enabled=0 is verified present in ${CMDLINE_FILE}."
    else
        log_info "Bootloader cmdline: zswap is deactivated via tmpfiles.d (clean userspace disable)."
    fi
fi

# --- 7. Configure zram0 via systemd-zram-generator ---
readonly CONFIG_DIR="/etc/systemd/zram-generator.conf.d"
readonly CONFIG_FILE="${CONFIG_DIR}/99-zram0.conf"
install -d -m 0755 "$CONFIG_DIR"

# Clean up legacy config files
rm -f "${CONFIG_DIR}/99-elite-zram.conf" \
      "${CONFIG_DIR}/99-elite-zram0.conf" \
      "${CONFIG_DIR}/99-memtune.conf"

tmp_config="$(umask 077 && mktemp)"
trap 'rm -f "$tmp_config"' EXIT

cat > "$tmp_config" <<EOF
# Managed by 205_zram_configuration.sh
[zram0]
zram-size = ${ZRAM_SIZE_EXPR}
zram-resident-limit = ${ZRAM_RESIDENT_LIMIT_EXPR}
compression-algorithm = ${COMPRESSION_ALGORITHM}
swap-priority = ${SWAP_PRIORITY}
options = discard
EOF

install -Dm0644 "$tmp_config" "$CONFIG_FILE"
log_success "ZRAM pool configuration written to ${CONFIG_FILE}"

# --- 8. Reload and Safe Lifecycle Management ---
log_info "Reloading systemd daemon to ingest generator configuration..."
systemctl daemon-reload

readonly ZRAM_SWAP_DEV="/dev/zram0"
readonly SWAP_SETUP_UNIT="systemd-zram-setup@zram0.service"
readonly SWAP_UNIT="dev-zram0.swap"

unit_is_loaded() {
    [[ "$(systemctl show -p LoadState --value "$1" 2>/dev/null || true)" == "loaded" ]]
}

if unit_is_loaded "$SWAP_SETUP_UNIT" && unit_is_loaded "$SWAP_UNIT"; then
    if swapon --show=NAME --noheadings | grep -qx "$ZRAM_SWAP_DEV"; then
        log_info "Active swap detected on $ZRAM_SWAP_DEV. Attempting safe swap recycling..."
        if ! swapoff "$ZRAM_SWAP_DEV" 2>/dev/null; then
            log_warn "Cannot safely swapoff $ZRAM_SWAP_DEV (swap is actively holding pages)."
            log_warn "New ZRAM configuration is safely staged and will activate on next reboot."
            exit 0
        fi
    fi

    if [[ -b "$ZRAM_SWAP_DEV" && -w "/sys/block/zram0/reset" ]]; then
        echo 1 > "/sys/block/zram0/reset" 2>/dev/null || true
    fi

    systemctl restart "$SWAP_SETUP_UNIT" 2>/dev/null || true
    systemctl restart "$SWAP_UNIT" 2>/dev/null || true
    if swapon --show=NAME --noheadings 2>/dev/null | grep -qx "$ZRAM_SWAP_DEV"; then
        log_success "ZRAM swap (${COMPRESSION_ALGORITHM} @ Priority ${SWAP_PRIORITY}) active and verified."
    else
        log_warn "ZRAM generator units reloaded. Swap device will activate cleanly on next boot."
    fi
else
    log_info "ZRAM generator units staged. New configuration will activate automatically on boot."
fi

exit 0

