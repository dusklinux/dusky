#!/usr/bin/env bash
# =============================================================================
# Elite Arch Linux ZRAM & VM Policy Optimizer
# Target: Arch Linux Cutting-Edge (Kernel 7.0+, Bash 5.3+)
# Scope: Platinum Grade. Pure performance, robust CLI, strict safety checks.
# Priority: Absolute Minimum RAM Footprint, BBR Networking, Max RAM Recovery.
# Updates: Synthesized exactly to Kernel 7.0 forensic tuning matrices & user overrides.
# =============================================================================

set -euo pipefail

readonly CONFIG_FILE="/etc/sysctl.d/99-vm-zram-parameters.conf"
readonly MGLRU_CONFIG="/etc/tmpfiles.d/99-mglru-optimize.conf"
readonly SCRIPT_NAME="${0##*/}"

# --- Strict Path Resolution (Coreutils required) ---
readonly SELF_PATH="$(realpath -e -- "${BASH_SOURCE[0]}")"

# --- Formatting ---
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
  --aggressive, -A     Force 32GB+ "Absolute Max" RAM usage profile
  --standard, -S       Force <32GB "Dynamic Efficiency" RAM savings profile
  --dry-run, -n        Print the generated config and exit without applying
  --help, -h           Show this help menu
EOF
}

usage_error() { log_error "$1"; print_help >&2; exit 2; }

# --- 1. CLI Parsing ---
MODE="AUTO"
declare -i DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --auto|-a)           MODE="AUTO"; shift ;;
        --aggressive|-A)     MODE="AGGRESSIVE"; shift ;;
        --standard|-S)       MODE="STANDARD"; shift ;;
        --dry-run|-n)        DRY_RUN=1; shift ;;
        --help|-h)           print_help; exit 0 ;;
        *)                   usage_error "Unknown argument: $1" ;;
    esac
done

# --- 2. Privilege Escalation ---
if [[ $EUID -ne 0 && $DRY_RUN -eq 0 ]]; then
    command -v sudo >/dev/null 2>&1 || die "'sudo' is not available."
    log_info "Root privileges required. Escalating..."
    exec sudo -- /usr/bin/bash "$SELF_PATH" "$@"
fi

# --- 3. System State Detection (Pure Bash 5.3 Regex) ---
declare -i SYSTEM_RAM_GB=0
declare -i ACTIVE_ZRAM_COUNT=0
declare -i ACTIVE_OTHER_COUNT=0
ZRAM_MAX_PRIO=""
OTHER_MAX_PRIO=""

if [[ $(< /proc/meminfo) =~ MemTotal:[[:space:]]+([0-9]+) ]]; then
    SYSTEM_RAM_GB=$(( BASH_REMATCH[1] / 1048576 ))
else
    die "FATAL: Could not parse /proc/meminfo natively."
fi

while read -r path _ _ _ prio; do
    [[ "$path" == "Filename" ]] && continue
    if [[ "$path" == /dev/zram* ]]; then
        ACTIVE_ZRAM_COUNT+=1
        if [[ -z "$ZRAM_MAX_PRIO" || "$prio" -gt "$ZRAM_MAX_PRIO" ]]; then ZRAM_MAX_PRIO="$prio"; fi
    elif [[ -n "$path" ]]; then
        ACTIVE_OTHER_COUNT+=1
        if [[ -z "$OTHER_MAX_PRIO" || "$prio" -gt "$OTHER_MAX_PRIO" ]]; then OTHER_MAX_PRIO="$prio"; fi
    fi
done < /proc/swaps

SWAP_LAYOUT="NONE"
if (( ACTIVE_ZRAM_COUNT > 0 && ACTIVE_OTHER_COUNT > 0 )); then
    SWAP_LAYOUT="HYBRID"
elif (( ACTIVE_ZRAM_COUNT > 0 )); then
    SWAP_LAYOUT="ZRAM_ONLY"
elif (( ACTIVE_OTHER_COUNT > 0 )); then
    SWAP_LAYOUT="DISK_ONLY"
fi

# --- 4. Tuning Profile Resolution ---
declare -i EXPECTED_SWAPPINESS
declare -i EXPECTED_VFS_PRESSURE
declare -i EXPECTED_SCALE_FACTOR
declare -i EXPECTED_DIRTY_BYTES
declare -i EXPECTED_DIRTY_BG_BYTES
declare -i EXPECTED_DIRTY_WRITEBACK
declare -i EXPECTED_DIRTY_EXPIRE
declare -i EXPECTED_MGLRU_TTL

# The 30 GB Demarcation Line
if [[ "$MODE" == "AGGRESSIVE" ]] || [[ "$MODE" == "AUTO" && SYSTEM_RAM_GB -ge 30 ]]; then
    EXPECTED_MODE="PERFORMANCE_LEAN (32GB+)"
    EXPECTED_SWAPPINESS=150
    EXPECTED_VFS_PRESSURE=100
    EXPECTED_SCALE_FACTOR=100
    EXPECTED_DIRTY_BYTES=1073741824
    EXPECTED_DIRTY_BG_BYTES=268435456
    EXPECTED_DIRTY_WRITEBACK=500
    EXPECTED_DIRTY_EXPIRE=3000
    EXPECTED_MGLRU_TTL=1000
else
    EXPECTED_MODE="STRICT_RAM_SAVINGS (<32GB)"
    EXPECTED_SWAPPINESS=190        # Force immediate compression of inactive RAM (User Override)
    EXPECTED_VFS_PRESSURE=200      # Aggressively reclaim inode/dentry VFS caches to lower idle RAM
    EXPECTED_SCALE_FACTOR=15       # 1.5% Emergency Buffer (120MB on 8GB RAM). Prevents UI direct reclaim stall.
    EXPECTED_DIRTY_BYTES=134217728 # 128MB max. Prevents massive file transfers from bloating RAM.
    EXPECTED_DIRTY_BG_BYTES=33554432 # 32MB bg threshold. Flushes data to disk sooner to free memory.
    EXPECTED_DIRTY_WRITEBACK=1000   # 10s dirty background page writeback interval
    EXPECTED_DIRTY_EXPIRE=500       # 5s dirty page expiry limit (flushes cache aggressively)
    EXPECTED_MGLRU_TTL=100          # Set to 100 to align with CachyOS. Speeds up idle page reclamation.
fi

# Static Constants
readonly EXPECTED_PAGE_CLUSTER=0        # Disables swap readahead (critical for ZRAM latency).
readonly EXPECTED_BOOST_FACTOR=0        # Disables sudden fragmentation CPU spikes.
readonly EXPECTED_COMPACTION=0          # Disables idle background CPU memory compaction.
readonly EXPECTED_MAX_MAP_COUNT=2147483 # Caps vm_area_struct bloat, high enough for gaming compatibility.

# --- 5. Generation & Verification ---
log_info "Initializing Platinum ZRAM & VM Policy Optimizer..."
log_info "Detected System RAM: ${C_BOLD}${SYSTEM_RAM_GB} GB${C_RESET}"
log_info "Detected Swap Layout: ${C_BOLD}${SWAP_LAYOUT}${C_RESET} (${ACTIVE_ZRAM_COUNT} ZRAM / ${ACTIVE_OTHER_COUNT} Disk)"

if [[ "$SWAP_LAYOUT" == "DISK_ONLY" || "$SWAP_LAYOUT" == "NONE" ]]; then
    die "Active ZRAM swap is required to utilize this tuning profile."
fi

# Priority Inversion Safety Guard
if [[ "$SWAP_LAYOUT" == "HYBRID" && -n "$ZRAM_MAX_PRIO" && -n "$OTHER_MAX_PRIO" ]]; then
    if (( ZRAM_MAX_PRIO <= OTHER_MAX_PRIO )); then
        log_warn "PRIORITY INVERSION DETECTED: Physical disk priority (${OTHER_MAX_PRIO}) is >= ZRAM priority (${ZRAM_MAX_PRIO})."
        log_warn "This will cause severe SSD thrashing because swappiness is locked at ${EXPECTED_SWAPPINESS}."
        log_warn "Fix your /etc/systemd/zram-generator.conf.d/ priority immediately."
    else
        log_info "Safety Check Passed: ZRAM (${ZRAM_MAX_PRIO}) overrides Disk (${OTHER_MAX_PRIO})."
    fi
fi

if [[ "$MODE" != "AUTO" ]]; then
    log_warn "Manual Override Engaged: Cache Mode forced to ${C_BOLD}${EXPECTED_MODE}${C_RESET}"
fi

# Secure temp file generation
tmpfile="$(umask 077 && mktemp)"
tmpfile_mglru="$(umask 077 && mktemp)"
trap 'rm -f "$tmpfile" "$tmpfile_mglru"' EXIT

# --- SYSCTL Payload ---
cat > "$tmpfile" <<EOF
# Managed by ${SCRIPT_NAME}
# Scope: Comprehensive ZRAM, Desktop Performance, & Network Matrix
# Detected State: Layout=${SWAP_LAYOUT}, Desktop Mode=${EXPECTED_MODE}, RAM=${SYSTEM_RAM_GB}GB

# --- SWAP CONFIGURATION ---
vm.swappiness = ${EXPECTED_SWAPPINESS}
vm.page-cluster = ${EXPECTED_PAGE_CLUSTER}

# --- DESKTOP SNAPPINESS (VFS & CACHE) ---
vm.vfs_cache_pressure = ${EXPECTED_VFS_PRESSURE}
vm.dirty_bytes = ${EXPECTED_DIRTY_BYTES}
vm.dirty_background_bytes = ${EXPECTED_DIRTY_BG_BYTES}
vm.dirty_writeback_centisecs = ${EXPECTED_DIRTY_WRITEBACK}
vm.dirty_expire_centisecs = ${EXPECTED_DIRTY_EXPIRE}

# --- MEMORY ALLOCATION & COMPACTION ---
vm.watermark_scale_factor = ${EXPECTED_SCALE_FACTOR}
vm.watermark_boost_factor = ${EXPECTED_BOOST_FACTOR}
vm.compaction_proactiveness = ${EXPECTED_COMPACTION}

# --- APPLICATION COMPATIBILITY ---
vm.max_map_count = ${EXPECTED_MAX_MAP_COUNT}

# --- MODERN NETWORK STACK (BBR + CAKE) ---
net.ipv4.tcp_congestion_control = bbr
net.core.default_qdisc = cake
net.ipv4.tcp_rmem = 4096 65536 4194304
net.ipv4.tcp_wmem = 4096 65536 4194304
net.core.netdev_max_backlog = 1000

# --- eBPF SECURITY & MEMORY COMPACTION ---
net.core.bpf_jit_enable = 1
net.core.bpf_jit_harden = 0
EOF

# --- MGLRU Payload ---
cat > "$tmpfile_mglru" <<EOF
# Managed by ${SCRIPT_NAME}
# Scope: MGLRU ZRAM Thrash Protection (CPU Shield)
# Description: ${EXPECTED_MGLRU_TTL}ms NVMe/ZRAM threshold. Prevents hot pages from being repeatedly compressed/decompressed.
w /sys/kernel/mm/lru_gen/min_ttl_ms - - - - ${EXPECTED_MGLRU_TTL}
EOF

# Dry Run Check
if (( DRY_RUN == 1 )); then
    log_info "DRY RUN EXECUTED. Generated configurations:"
    echo -e "\n${C_BOLD}[ ${CONFIG_FILE} ]${C_RESET}"
    cat "$tmpfile"
    echo -e "\n${C_BOLD}[ ${MGLRU_CONFIG} ]${C_RESET}"
    cat "$tmpfile_mglru"
    exit 0
fi

# --- Apply Sysctl ---
if [[ -f "$CONFIG_FILE" ]] && cmp -s "$tmpfile" "$CONFIG_FILE"; then
    log_info "Sysctl configuration already matches desired state."
else
    install -Dm0644 "$tmpfile" "$CONFIG_FILE"
    log_success "Configuration written to ${CONFIG_FILE}"
fi

log_info "Applying sysctl parameters to live kernel..."
sysctl --load "$CONFIG_FILE" >/dev/null || die "Failed to apply sysctl settings."

# --- Apply MGLRU Tmpfiles ---
if [[ -d "/sys/kernel/mm/lru_gen" ]]; then
    if [[ -f "$MGLRU_CONFIG" ]] && cmp -s "$tmpfile_mglru" "$MGLRU_CONFIG"; then
        log_info "MGLRU configuration already matches desired state."
    else
        install -Dm0644 "$tmpfile_mglru" "$MGLRU_CONFIG"
        log_success "MGLRU Protection written to ${MGLRU_CONFIG}"
    fi
    
    log_info "Applying MGLRU parameters to live kernel..."
    systemd-tmpfiles --create "$MGLRU_CONFIG" || log_warn "Failed to apply systemd-tmpfiles for MGLRU."
else
    log_warn "MGLRU is not enabled in this kernel. Skipping min_ttl_ms protection."
fi

# --- Configure Security & Systemd NOFILE limits ---
log_info "Optimizing open file limits (LimitNOFILE) for systemd and PAM..."

tmpfile_limits="$(umask 077 && mktemp)"
tmpfile_sysd="$(umask 077 && mktemp)"
trap 'rm -f "$tmpfile" "$tmpfile_mglru" "$tmpfile_limits" "$tmpfile_sysd"' EXIT

# PAM limits
cat > "$tmpfile_limits" <<EOF
# Managed by ${SCRIPT_NAME}
# Increase open file limits for heavy parallel applications
* soft nofile 65536
* hard nofile 524288
EOF

if [[ -f "/etc/security/limits.d/99-nofile-limits.conf" ]] && cmp -s "$tmpfile_limits" "/etc/security/limits.d/99-nofile-limits.conf"; then
    log_info "PAM limits configuration already matches desired state."
else
    install -Dm0644 "$tmpfile_limits" "/etc/security/limits.d/99-nofile-limits.conf"
    log_success "PAM limits written to /etc/security/limits.d/99-nofile-limits.conf"
fi

# Systemd limits
cat > "$tmpfile_sysd" <<EOF
# Managed by ${SCRIPT_NAME}
[Manager]
DefaultLimitNOFILE=65536:524288
EOF

# Write to system.conf.d
if [[ -f "/etc/systemd/system.conf.d/99-nofile-limits.conf" ]] && cmp -s "$tmpfile_sysd" "/etc/systemd/system.conf.d/99-nofile-limits.conf"; then
    log_info "Systemd system limits configuration already matches desired state."
else
    install -Dm0644 "$tmpfile_sysd" "/etc/systemd/system.conf.d/99-nofile-limits.conf"
    log_success "Systemd system limits written to /etc/systemd/system.conf.d/99-nofile-limits.conf"
    systemctl daemon-reexec || true
fi

# Write to user.conf.d
if [[ -f "/etc/systemd/user.conf.d/99-nofile-limits.conf" ]] && cmp -s "$tmpfile_sysd" "/etc/systemd/user.conf.d/99-nofile-limits.conf"; then
    log_info "Systemd user limits configuration already matches desired state."
else
    install -Dm0644 "$tmpfile_sysd" "/etc/systemd/user.conf.d/99-nofile-limits.conf"
    log_success "Systemd user limits written to /etc/systemd/user.conf.d/99-nofile-limits.conf"
    systemctl daemon-reexec || true
    # Re-exec user manager instance for active desktop sessions
    while read -r uid; do
        if [[ "$uid" =~ ^[0-9]+$ ]]; then
            runuser -u dusk -- systemctl --user daemon-reexec >/dev/null 2>&1 || true
        fi
    done < <(pgrep -u root -f "systemd --user" | xargs -r -I {} sh -c 'cat /proc/{}/loginuid' 2>/dev/null || true)
fi

# --- Hardened Live Verification ---
actual_swappiness="$(< /proc/sys/vm/swappiness)"
actual_vfs="$(< /proc/sys/vm/vfs_cache_pressure)"
actual_scale="$(< /proc/sys/vm/watermark_scale_factor)"
actual_compaction="$(< /proc/sys/vm/compaction_proactiveness)"
actual_bpf="$(< /proc/sys/net/core/bpf_jit_harden)"

if [[ "$actual_swappiness" != "$EXPECTED_SWAPPINESS" ]]; then
    die "Verification failed: vm.swappiness is '${actual_swappiness}', expected '${EXPECTED_SWAPPINESS}'."
fi

if [[ "$actual_vfs" != "$EXPECTED_VFS_PRESSURE" ]]; then
    die "Verification failed: vm.vfs_cache_pressure is '${actual_vfs}', expected '${EXPECTED_VFS_PRESSURE}'."
fi

if [[ "$actual_scale" != "$EXPECTED_SCALE_FACTOR" ]]; then
    die "Verification failed: vm.watermark_scale_factor is '${actual_scale}', expected '${EXPECTED_SCALE_FACTOR}'."
fi

if [[ "$actual_compaction" != "$EXPECTED_COMPACTION" ]]; then
    die "Verification failed: vm.compaction_proactiveness is '${actual_compaction}', expected '${EXPECTED_COMPACTION}'."
fi

if [[ "$actual_bpf" != "0" ]]; then
    die "Verification failed: net.core.bpf_jit_harden is '${actual_bpf}', expected '0'."
fi

log_success "Verified live kernel values:"
log_success "  vm.swappiness = ${actual_swappiness} (Ideal ZRAM Reclaim)"
log_success "  vm.vfs_cache_pressure = ${actual_vfs} (Slab Reclaim Active)"
log_success "  vm.watermark_scale_factor = ${actual_scale} (Safe Direct Reclaim Buffer)"
log_success "  vm.compaction_proactiveness = ${actual_compaction}"
log_success "  net.core.bpf_jit_harden = ${actual_bpf} (Security Disabled / RAM Recovered)"

if [[ -f "/sys/kernel/mm/lru_gen/min_ttl_ms" ]]; then
    actual_ttl="$(< /sys/kernel/mm/lru_gen/min_ttl_ms)"
    if [[ "$actual_ttl" == "$EXPECTED_MGLRU_TTL" ]]; then
        log_success "  MGLRU min_ttl_ms = ${actual_ttl} (NVMe/ZRAM Thrash Protection Active)"
    else
        log_warn "  MGLRU min_ttl_ms verification failed. Read: ${actual_ttl}"
    fi
fi

log_success "  Active Tuning Profile: [${C_BOLD}${EXPECTED_MODE}${C_RESET}]"

exit 0
