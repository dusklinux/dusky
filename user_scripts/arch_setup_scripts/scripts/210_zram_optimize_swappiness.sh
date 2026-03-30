#!/usr/bin/env bash
# ZRAM & System Desktop VM Policy Optimizer
# -----------------------------------------------------------------------------
# Description:
#   Comprehensive VM sysctl tuning for Arch/Hyprland ecosystems.
#   Combines strict ZRAM memory allocation with aggressive VFS caching 
#   for maximum desktop snappiness, automatically scaling based on RAM size.
# -----------------------------------------------------------------------------

set -euo pipefail

# --- Configuration ---
readonly CONFIG_FILE="/etc/sysctl.d/99-vm-zram-parameters.conf"
readonly SCRIPT_NAME="${0##*/}"

# --- Optional color output ---
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    C_RESET=$'\033[0m'
    C_GREEN=$'\033[1;32m'
    C_BLUE=$'\033[1;34m'
    C_RED=$'\033[1;31m'
    C_YELLOW=$'\033[1;33m'
    C_BOLD=$'\033[1m'
else
    C_RESET=''
    C_GREEN=''
    C_BLUE=''
    C_RED=''
    C_YELLOW=''
    C_BOLD=''
fi

readonly C_RESET C_GREEN C_BLUE C_RED C_YELLOW C_BOLD

log_info()    { printf '%s[INFO]%s %s\n'  "$C_BLUE"   "$C_RESET" "$1"; }
log_success() { printf '%s[OK]%s %s\n'    "$C_GREEN"  "$C_RESET" "$1"; }
log_warn()    { printf '%s[WARN]%s %s\n'  "$C_YELLOW" "$C_RESET" "$1"; }
log_error()   { printf '%s[ERROR]%s %s\n' "$C_RED"    "$C_RESET" "$1" >&2; }

die() {
    log_error "$1"
    exit "${2:-1}"
}

resolve_self_path() {
    local src="${BASH_SOURCE[0]:-$0}"

    if [[ "$src" != */* ]]; then
        if [[ -e "$src" ]]; then
            src="./$src"
        else
            src="$(command -v "$src" 2>/dev/null || true)"
            [[ -n "$src" ]] || return 1
        fi
    fi

    printf '%s/%s\n' "$(cd -- "$(dirname -- "$src")" && pwd -P)" "$(basename -- "$src")"
}

readonly SELF_PATH="$(resolve_self_path)" || die "Failed to resolve script path for privilege escalation."

print_help() {
    cat <<EOF
${C_BOLD}Usage:${C_RESET} ${SCRIPT_NAME} [OPTIONS]

  --auto, -a           Auto-detect swap layout and RAM size (default)
  --hybrid, --disk, -d Force hybrid swap policy (zram + physical disk swap)
  --zram-only, -z      Force zram-only swap policy
  --aggressive, -A     Force aggressive desktop VFS caching (Auto-enabled for 32GB+ RAM)
  --standard, -S       Force standard desktop VFS caching
  --dry-run, -n        Print the generated config and exit
  --help, -h           Show this help
EOF
}

usage_error() {
    log_error "$1"
    print_help >&2
    exit 2
}

# --- 1. Privilege Escalation ---
if [[ $EUID -ne 0 ]]; then
    command -v sudo >/dev/null 2>&1 || die "Root privileges are required and 'sudo' is not available."
    log_info "Root privileges required. Escalating..."
    exec sudo -- /usr/bin/bash "$SELF_PATH" "$@"
fi

# --- 2. Runtime Requirements ---
require_runtime() {
    [[ -r /proc/swaps ]] || die "Cannot read /proc/swaps."
    [[ -r /proc/meminfo ]] || die "Cannot read /proc/meminfo."
    [[ -r /proc/sys/vm/swappiness ]] || die "Kernel tunable /proc/sys/vm/swappiness is unavailable."
    [[ -r /proc/sys/vm/vfs_cache_pressure ]] || die "Kernel tunable /proc/sys/vm/vfs_cache_pressure is unavailable."

    command -v sysctl >/dev/null 2>&1 || die "'sysctl' command not found."
    command -v install >/dev/null 2>&1 || die "'install' command not found."
    command -v mktemp >/dev/null 2>&1 || die "'mktemp' command not found."
    command -v cmp >/dev/null 2>&1 || die "'cmp' command not found."
}

# --- 3. System State Detection ---
SWAP_LAYOUT="NONE"
ZRAM_MAX_PRIO=""
OTHER_MAX_PRIO=""
ACTIVE_ZRAM_COUNT=0
ACTIVE_OTHER_COUNT=0
SYSTEM_RAM_GB=0

detect_system_state() {
    local path type size used prio
    local has_zram=0
    local has_other=0
    local mem_kb

    # A. Detect RAM Size
    while read -r key value unit; do
        if [[ "$key" == "MemTotal:" ]]; then
            mem_kb=$value
            break
        fi
    done < /proc/meminfo
    SYSTEM_RAM_GB=$(( mem_kb / 1024 / 1024 ))

    # B. Detect Swap Layout
    while read -r path type size used prio; do
        [[ -z "${path:-}" || "$path" == "Filename" ]] && continue

        if [[ "$path" =~ ^/dev/zram[0-9]+$ ]]; then
            has_zram=1
            ((ACTIVE_ZRAM_COUNT += 1))
            if [[ -z "$ZRAM_MAX_PRIO" || "$prio" -gt "$ZRAM_MAX_PRIO" ]]; then
                ZRAM_MAX_PRIO="$prio"
            fi
        else
            has_other=1
            ((ACTIVE_OTHER_COUNT += 1))
            if [[ -z "$OTHER_MAX_PRIO" || "$prio" -gt "$OTHER_MAX_PRIO" ]]; then
                OTHER_MAX_PRIO="$prio"
            fi
        fi
    done < /proc/swaps

    if (( has_zram == 1 && has_other == 1 )); then
        SWAP_LAYOUT="HYBRID"
    elif (( has_zram == 1 )); then
        SWAP_LAYOUT="ZRAM_ONLY"
    elif (( has_other == 1 )); then
        SWAP_LAYOUT="DISK_ONLY"
    else
        SWAP_LAYOUT="NONE"
    fi
}

# --- 4. Tuning Profile Resolution ---
resolve_tuning_profile() {
    local target_layout="$1"
    local target_mode="$2"

    # Swap Logic
    if [[ "$target_layout" == "ZRAM_ONLY" ]]; then
        EXPECTED_SWAPPINESS=180
    else
        EXPECTED_SWAPPINESS=133 # Protect physical disk from thrashing
    fi

    # Caching/Desktop Logic
    # Use 29 to account for hardware-reserved RAM on 32GB physical machines
    if [[ "$target_mode" == "AGGRESSIVE" ]] || [[ "$target_mode" == "AUTO" && "$SYSTEM_RAM_GB" -ge 29 ]]; then
        EXPECTED_MODE="AGGRESSIVE"
        EXPECTED_VFS_PRESSURE=50
        EXPECTED_SCALE_FACTOR=150
        EXPECTED_DIRTY_BYTES=1073741824
        EXPECTED_DIRTY_BG_BYTES=268435456
    else
        EXPECTED_MODE="STANDARD"
        EXPECTED_VFS_PRESSURE=100
        EXPECTED_SCALE_FACTOR=125
        EXPECTED_DIRTY_BYTES=268435456
        EXPECTED_DIRTY_BG_BYTES=67108864
    fi

    # Static Constants
    EXPECTED_PAGE_CLUSTER=0
    EXPECTED_BOOST_FACTOR=0
    EXPECTED_COMPACTION=20
    EXPECTED_MIN_FREE=131072
    EXPECTED_MAX_MAP_COUNT=2147483642
}

generate_config() {
    cat <<EOF
# Managed by ${SCRIPT_NAME}
# Scope: Comprehensive ZRAM & Desktop Performance VM policy
# Detected State: Layout=${1}, Desktop Mode=${EXPECTED_MODE}, RAM=${SYSTEM_RAM_GB}GB

# --- SWAP CONFIGURATION ---
# Swappiness: 180 for pure ZRAM, 133 to protect hybrid disk setups
vm.swappiness = ${EXPECTED_SWAPPINESS}
vm.page-cluster = ${EXPECTED_PAGE_CLUSTER}

# --- DESKTOP SNAPPINESS (VFS & CACHE) ---
# Lower pressure retains directory maps longer for instant folder opening
vm.vfs_cache_pressure = ${EXPECTED_VFS_PRESSURE}

# Heavy NVMe write smoothing
vm.dirty_bytes = ${EXPECTED_DIRTY_BYTES}
vm.dirty_background_bytes = ${EXPECTED_DIRTY_BG_BYTES}

# --- MEMORY ALLOCATION & COMPACTION ---
# Prevent Direct Reclaim desktop stutters without starving the system
vm.watermark_scale_factor = ${EXPECTED_SCALE_FACTOR}
vm.watermark_boost_factor = ${EXPECTED_BOOST_FACTOR}
vm.compaction_proactiveness = ${EXPECTED_COMPACTION}
vm.min_free_kbytes = ${EXPECTED_MIN_FREE}

# --- APPLICATION COMPATIBILITY ---
# High limit for heavy Windows games via Proton/Wine
vm.max_map_count = ${EXPECTED_MAX_MAP_COUNT}
EOF
}

write_config_if_changed() {
    local src="$1"

    if [[ -f "$CONFIG_FILE" ]] && cmp -s "$src" "$CONFIG_FILE"; then
        log_info "Configuration file already matches desired state. No disk write needed."
    else
        install -Dm0644 "$src" "$CONFIG_FILE"
        log_success "Configuration written to ${CONFIG_FILE}"
    fi
}

apply_and_verify() {
    log_info "Applying sysctl parameters to live kernel..."
    sysctl --load "$CONFIG_FILE" >/dev/null || die "Failed to apply sysctl settings from ${CONFIG_FILE}."

    # Live Verification of key parameters
    local actual_swappiness="$(< /proc/sys/vm/swappiness)"
    local actual_vfs="$(< /proc/sys/vm/vfs_cache_pressure)"

    [[ "$actual_swappiness" == "$EXPECTED_SWAPPINESS" ]] || die \
        "Verification failed: vm.swappiness is '${actual_swappiness}', expected '${EXPECTED_SWAPPINESS}'."

    [[ "$actual_vfs" == "$EXPECTED_VFS_PRESSURE" ]] || die \
        "Verification failed: vm.vfs_cache_pressure is '${actual_vfs}', expected '${EXPECTED_VFS_PRESSURE}'."

    log_success "Verified live kernel values:"
    log_success "  vm.swappiness = ${actual_swappiness}"
    log_success "  vm.vfs_cache_pressure = ${actual_vfs}"
    log_success "  Full Desktop Mode [${EXPECTED_MODE}] successfully engaged."
}

# --- 5. CLI Parsing ---
LAYOUT="AUTO"
MODE="AUTO"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --auto|-a)           LAYOUT="AUTO"; MODE="AUTO"; shift ;;
        --hybrid|--disk|-d)  LAYOUT="HYBRID"; shift ;;
        --zram-only|-z)      LAYOUT="ZRAM_ONLY"; shift ;;
        --aggressive|-A)     MODE="AGGRESSIVE"; shift ;;
        --standard|-S)       MODE="STANDARD"; shift ;;
        --dry-run|-n)        DRY_RUN=1; shift ;;
        --help|-h)           print_help; exit 0 ;;
        *)                   usage_error "Unknown argument: $1" ;;
    esac
done

# --- 6. Execution Flow ---
require_runtime
detect_system_state

log_info "Initializing Elite ZRAM & VM Policy Optimizer..."
log_info "Detected System RAM: ${SYSTEM_RAM_GB} GB"

# Layout Output Logging
case "$SWAP_LAYOUT" in
    HYBRID)
        log_info "Detected ${ACTIVE_ZRAM_COUNT} active zram device(s) and ${ACTIVE_OTHER_COUNT} non-zram swap device(s)."
        ;;
    ZRAM_ONLY)
        log_info "Detected ${ACTIVE_ZRAM_COUNT} active zram device(s)."
        ;;
    DISK_ONLY)
        log_info "Detected ${ACTIVE_OTHER_COUNT} non-zram swap device(s), but no active zram swap."
        ;;
    NONE)
        log_info "No active swap devices detected."
        ;;
esac

# Resolve Auto Layout
if [[ "$LAYOUT" == "AUTO" ]]; then
    case "$SWAP_LAYOUT" in
        HYBRID|ZRAM_ONLY)
            LAYOUT="$SWAP_LAYOUT"
            log_info "Auto-detected Swap Layout: ${C_BOLD}${LAYOUT}${C_RESET}"
            ;;
        DISK_ONLY)
            die "Active non-zram swap was detected, but no active zram swap is present. This script targets ZRAM architectures."
            ;;
        NONE)
            die "No active swap devices were detected in /proc/swaps. Nothing to tune."
            ;;
    esac
else
    log_warn "Manual override: Swap layout forced to ${C_BOLD}${LAYOUT}${C_RESET}"
fi

# Trap: Priority Check (from Claude)
if [[ "$SWAP_LAYOUT" == "HYBRID" && -n "$ZRAM_MAX_PRIO" && -n "$OTHER_MAX_PRIO" ]]; then
    if (( ZRAM_MAX_PRIO <= OTHER_MAX_PRIO )); then
        log_warn "Hybrid swap is active, but ZRAM priority (${ZRAM_MAX_PRIO}) is not strictly above disk swap priority (${OTHER_MAX_PRIO})."
        log_warn "Swappiness alone cannot guarantee ZRAM is preferred. Ensure your zram-generator config sets a high priority."
    fi
fi

# Resolve Tuning Profile Data
resolve_tuning_profile "$LAYOUT" "$MODE"
if [[ "$MODE" == "AUTO" ]]; then
    log_info "Auto-detected Cache Mode: ${C_BOLD}${EXPECTED_MODE}${C_RESET} (Threshold: 32GB RAM)"
else
    log_warn "Manual override: Cache Mode forced to ${C_BOLD}${EXPECTED_MODE}${C_RESET}"
fi

# Write & Verify
tmpfile="$(mktemp)"
trap 'rm -f "$tmpfile"' EXIT

generate_config "$LAYOUT" > "$tmpfile"

if (( DRY_RUN == 1 )); then
    cat "$tmpfile"
    exit 0
fi

write_config_if_changed "$tmpfile"
apply_and_verify

exit 0
