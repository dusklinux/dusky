#!/usr/bin/env bash
# Tuner for ZRAM Swappiness, Virtual Memory Paging, and MGLRU Heuristics
# Target: Arch Linux (Linux Kernel 7.2+, systemd 261+)
# Scope: Focused strictly on VM memory balance and ZRAM paging efficiency.

set -euo pipefail

readonly CONFIG_FILE="/etc/sysctl.d/99-vm-zram-parameters.conf"
readonly MGLRU_CONFIG="/etc/tmpfiles.d/99-mglru-optimize.conf"
readonly SCRIPT_NAME="${0##*/}"
ORIG_ARGS=("$@")
readonly SELF_PATH="$(realpath -e -- "${BASH_SOURCE[0]}")"

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

  --auto, -a           Auto-detect RAM size and set dynamic profile (default)
  --performance, -p    Force >=32GB class "Performance Lean" profile
  --savings, -s        Force <32GB class "Strict Dynamic Efficiency" profile
  --dry-run, -n        Print the generated configuration and exit
  --help, -h           Show this help menu
EOF
}

usage_error() { log_error "$1"; print_help >&2; exit 2; }

MODE="AUTO"
declare -i DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --auto|-a)                                       MODE="AUTO"; shift ;;
        --performance|-p|-A|--aggressive)               MODE="PERFORMANCE"; shift ;;
        --savings|-s|-S|--standard|--efficiency)        MODE="SAVINGS"; shift ;;
        --dry-run|-n)                                    DRY_RUN=1; shift ;;
        --help|-h)                                       print_help; exit 0 ;;
        *)                                               usage_error "Unknown argument: $1" ;;
    esac
done

if [[ $EUID -ne 0 && $DRY_RUN -eq 0 ]]; then
    command -v sudo >/dev/null 2>&1 || die "'sudo' is not available."
    log_info "Root privileges required. Escalating..."
    exec sudo -- /usr/bin/bash "$SELF_PATH" "${ORIG_ARGS[@]}"
fi

declare -i SYSTEM_RAM_KB=0
declare -i SYSTEM_RAM_GB=0
declare -i ACTIVE_ZRAM_COUNT=0
declare -i ACTIVE_DISK_COUNT=0
ZRAM_MAX_PRIO=""
DISK_MAX_PRIO=""

if [[ $(< /proc/meminfo) =~ MemTotal:[[:space:]]+([0-9]+) ]]; then
    SYSTEM_RAM_KB=$(( BASH_REMATCH[1] ))
    SYSTEM_RAM_GB=$(( (SYSTEM_RAM_KB + 524288) / 1048576 ))
else
    die "FATAL: Could not parse /proc/meminfo natively."
fi

if [[ -f /proc/swaps ]]; then
    while read -r path _ _ _ prio; do
        [[ "$path" == "Filename" ]] && continue
        if [[ "$path" == /dev/zram* ]]; then
            ACTIVE_ZRAM_COUNT+=1
            if [[ -z "$ZRAM_MAX_PRIO" || "$prio" -gt "$ZRAM_MAX_PRIO" ]]; then ZRAM_MAX_PRIO="$prio"; fi
        elif [[ -n "$path" ]]; then
            ACTIVE_DISK_COUNT+=1
            if [[ -z "$DISK_MAX_PRIO" || "$prio" -gt "$DISK_MAX_PRIO" ]]; then DISK_MAX_PRIO="$prio"; fi
        fi
    done < /proc/swaps
fi

if (( ACTIVE_ZRAM_COUNT == 0 )); then
    die "FATAL: No active ZRAM device detected in /proc/swaps. This high-swappiness profile requires ZRAM swap."
fi

SWAP_LAYOUT="NONE"
if (( ACTIVE_ZRAM_COUNT > 0 && ACTIVE_DISK_COUNT > 0 )); then
    SWAP_LAYOUT="HYBRID"
elif (( ACTIVE_ZRAM_COUNT > 0 )); then
    SWAP_LAYOUT="ZRAM_ONLY"
elif (( ACTIVE_DISK_COUNT > 0 )); then
    SWAP_LAYOUT="DISK_ONLY"
fi

if [[ "$SWAP_LAYOUT" == "HYBRID" && -n "$ZRAM_MAX_PRIO" && -n "$DISK_MAX_PRIO" ]]; then
    if (( ZRAM_MAX_PRIO <= DISK_MAX_PRIO )); then
        log_warn "PRIORITY INVERSION: Disk swap prio (${DISK_MAX_PRIO}) >= ZRAM prio (${ZRAM_MAX_PRIO})."
        log_warn "With high swappiness, disk will be hit before ZRAM. Set ZRAM priority higher (e.g., 32767)."
    fi
fi

declare -i EXPECTED_SWAPPINESS
declare -i EXPECTED_VFS_PRESSURE
declare -i EXPECTED_SCALE_FACTOR
declare -i EXPECTED_DIRTY_BYTES
declare -i EXPECTED_DIRTY_BG_BYTES
declare -i EXPECTED_MGLRU_TTL

# Unified 4-Tier Memory Demarcation
# S:  < 7 GiB       (< 7,340,032 KiB)
# M:  7 - < 14 GiB  (7,340,032 - < 14,680,064 KiB)
# L:  14 - < 28 GiB (14,680,064 - < 29,360,128 KiB)
# XL: >= 28 GiB     (>= 29,360,128 KiB, captures 32GB+ systems with iGPU/UMA carve-outs)

if [[ "$MODE" == "PERFORMANCE" ]] || { [[ "$MODE" == "AUTO" ]] && (( SYSTEM_RAM_KB >= 29360128 )); }; then
    PROFILE_NAME="PERFORMANCE_LEAN (>=32GB class)"
    EXPECTED_SWAPPINESS=150
    EXPECTED_VFS_PRESSURE=50
    EXPECTED_SCALE_FACTOR=30             # 30 = ~98MB (32GB) / ~196MB (64GB) kswapd headroom
    EXPECTED_DIRTY_BYTES=536870912       # 512MiB cap prevents massive multi-GB writeback stalls
    EXPECTED_DIRTY_BG_BYTES=134217728    # 128MiB background flush
    EXPECTED_MGLRU_TTL=0                 # 0ms prevents premature OOM under tight memory
elif (( SYSTEM_RAM_KB >= 14680064 )); then
    PROFILE_NAME="BALANCED_EFFICIENCY (16-24GB class)"
    EXPECTED_SWAPPINESS=180
    EXPECTED_VFS_PRESSURE=125
    EXPECTED_SCALE_FACTOR=50             # 50 = ~80-140MB kswapd runway to prevent direct reclaim stalls
    EXPECTED_DIRTY_BYTES=134217728       # 128MiB cap
    EXPECTED_DIRTY_BG_BYTES=33554432     # 32MiB background flush
    EXPECTED_MGLRU_TTL=0
elif (( SYSTEM_RAM_KB >= 7340032 )); then
    PROFILE_NAME="DYNAMIC_EFFICIENCY (8-12GB class)"
    EXPECTED_SWAPPINESS=180
    EXPECTED_VFS_PRESSURE=125
    EXPECTED_SCALE_FACTOR=75             # 75 = ~60-100MB kswapd runway
    EXPECTED_DIRTY_BYTES=134217728       # 128MiB cap
    EXPECTED_DIRTY_BG_BYTES=33554432     # 32MiB background flush
    EXPECTED_MGLRU_TTL=0
else
    PROFILE_NAME="DYNAMIC_EFFICIENCY (<8GB class)"
    EXPECTED_SWAPPINESS=180
    EXPECTED_VFS_PRESSURE=125
    EXPECTED_SCALE_FACTOR=100            # 100 = 1% RAM runway (~40-70MB)
    EXPECTED_DIRTY_BYTES=134217728       # 128MiB cap
    EXPECTED_DIRTY_BG_BYTES=33554432     # 32MiB background flush
    EXPECTED_MGLRU_TTL=0
fi

readonly EXPECTED_PAGE_CLUSTER=0        # Disables swap readahead
readonly EXPECTED_BOOST_FACTOR=0        # Disables watermark boosting
readonly EXPECTED_COMPACTION=0          # Disables proactive background compaction
readonly EXPECTED_MAX_MAP_COUNT=2147483642 # SteamOS & modern Proton/Wine standard

log_info "Initializing VM Swappiness & Paging Optimizer..."
log_info "Detected RAM: ${C_BOLD}${SYSTEM_RAM_GB} GB${C_RESET} (${SYSTEM_RAM_KB} KiB)"
log_info "Detected Swap Topology: ${C_BOLD}${SWAP_LAYOUT}${C_RESET} (${ACTIVE_ZRAM_COUNT} ZRAM / ${ACTIVE_DISK_COUNT} Disk)"

if [[ "$MODE" != "AUTO" ]]; then
    log_warn "Manual Profile Override: Forced to [${C_BOLD}${PROFILE_NAME}${C_RESET}]"
fi

tmpfile_sysctl="$(umask 077 && mktemp)"
tmpfile_mglru="$(umask 077 && mktemp)"
trap 'rm -f "$tmpfile_sysctl" "$tmpfile_mglru"' EXIT

cat > "$tmpfile_sysctl" <<EOF
# Managed by ${SCRIPT_NAME}
# Profile: ${PROFILE_NAME} | Detected RAM: ${SYSTEM_RAM_GB}GB
# Target: Arch Linux / Kernel 7.2+ / systemd 261+

# --- ZRAM SWAP POLICY ---
vm.swappiness = ${EXPECTED_SWAPPINESS}
vm.page-cluster = ${EXPECTED_PAGE_CLUSTER}

# --- VFS & CACHE RECLAMATION ---
vm.vfs_cache_pressure = ${EXPECTED_VFS_PRESSURE}

# --- WATERMARK HEADROOM & LATENCY ---
vm.watermark_scale_factor = ${EXPECTED_SCALE_FACTOR}
vm.watermark_boost_factor = ${EXPECTED_BOOST_FACTOR}
vm.compaction_proactiveness = ${EXPECTED_COMPACTION}

# --- WRITEBACK (NVMe & SSD PROTECTION) ---
vm.dirty_bytes = ${EXPECTED_DIRTY_BYTES}
vm.dirty_background_bytes = ${EXPECTED_DIRTY_BG_BYTES}

# --- APPLICATION COMPATIBILITY & GAMING ---
vm.max_map_count = ${EXPECTED_MAX_MAP_COUNT}
EOF

cat > "$tmpfile_mglru" <<EOF
# Managed by ${SCRIPT_NAME}
# Scope: Multi-Gen LRU (MGLRU) runtime optimization for ZRAM
w- /sys/kernel/mm/lru_gen/enabled - - - - 0x0007
w- /sys/kernel/mm/lru_gen/min_ttl_ms - - - - ${EXPECTED_MGLRU_TTL}
EOF

if (( DRY_RUN == 1 )); then
    log_info "DRY RUN EXECUTED. Generated ${CONFIG_FILE}:"
    cat "$tmpfile_sysctl"
    echo "Generated ${MGLRU_CONFIG}:"
    cat "$tmpfile_mglru"
    exit 0
fi

if [[ -f "$CONFIG_FILE" ]] && cmp -s "$tmpfile_sysctl" "$CONFIG_FILE"; then
    log_info "Sysctl configuration already up to date in ${CONFIG_FILE}."
else
    install -Dm0644 "$tmpfile_sysctl" "$CONFIG_FILE"
    log_success "Wrote sysctl configuration to ${CONFIG_FILE}"
fi

log_info "Applying sysctl parameters to live kernel..."
if [[ -x "/usr/lib/systemd/systemd-sysctl" ]]; then
    /usr/lib/systemd/systemd-sysctl "$CONFIG_FILE" >/dev/null 2>&1 || sysctl -q --load "$CONFIG_FILE" >/dev/null 2>&1 || true
else
    sysctl -q --load "$CONFIG_FILE" >/dev/null 2>&1 || true
fi

if [[ -f "$MGLRU_CONFIG" ]] && cmp -s "$tmpfile_mglru" "$MGLRU_CONFIG"; then
    log_info "MGLRU configuration already up to date in ${MGLRU_CONFIG}."
else
    install -Dm0644 "$tmpfile_mglru" "$MGLRU_CONFIG"
    log_success "Wrote MGLRU tmpfiles configuration to ${MGLRU_CONFIG}"
fi

log_info "Applying MGLRU parameters via systemd-tmpfiles..."
systemd-tmpfiles --create "$MGLRU_CONFIG" >/dev/null 2>&1 || log_warn "systemd-tmpfiles finished with warnings (normal if MGLRU not compiled in kernel)."

verify_param() {
    local key="$1" expected="$2"
    local actual
    actual="$(sysctl -n "$key" 2>/dev/null || echo "MISSING")"
    if [[ "$actual" == "$expected" ]]; then
        log_success "  ${key} = ${actual}"
    else
        log_warn "  ${key} = ${actual} (expected: ${expected})"
    fi
}

log_info "Verifying applied kernel parameters:"
verify_param "vm.swappiness" "$EXPECTED_SWAPPINESS"
verify_param "vm.vfs_cache_pressure" "$EXPECTED_VFS_PRESSURE"
verify_param "vm.watermark_scale_factor" "$EXPECTED_SCALE_FACTOR"
verify_param "vm.watermark_boost_factor" "$EXPECTED_BOOST_FACTOR"
verify_param "vm.page-cluster" "$EXPECTED_PAGE_CLUSTER"
verify_param "vm.dirty_background_bytes" "$EXPECTED_DIRTY_BG_BYTES"
verify_param "vm.dirty_bytes" "$EXPECTED_DIRTY_BYTES"
verify_param "vm.max_map_count" "$EXPECTED_MAX_MAP_COUNT"

if [[ -f "/sys/kernel/mm/lru_gen/min_ttl_ms" ]]; then
    actual_ttl="$(cat /sys/kernel/mm/lru_gen/min_ttl_ms 2>/dev/null || echo "N/A")"
    log_success "  MGLRU min_ttl_ms = ${actual_ttl}"
fi

log_success "Profile [${C_BOLD}${PROFILE_NAME}${C_RESET}] successfully deployed."
exit 0

