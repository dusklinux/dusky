#!/usr/bin/env bash
# ==============================================================================
# 212_thp_sysfs_optimizer.sh
# Scope: Transparent HugePages (mTHP) & MGLRU sysfs configuration
# Target: Arch Linux / Kernel 7.2+ / systemd 261+
# Tuning: Strict RAM savings without performance compromise (<32GB focus)
# ==============================================================================

set -euo pipefail

readonly CONFIG_FILE="/etc/tmpfiles.d/99-thp-mglru-optimize.conf"
readonly SCRIPT_NAME="${0##*/}"
readonly THP_BASE_DIR="/sys/kernel/mm/transparent_hugepage"

orig_args=("$@")
SELF_PATH=""
if [[ -n "${BASH_SOURCE[0]:-}" && -e "${BASH_SOURCE[0]}" ]]; then
    SELF_PATH="$(realpath -e -- "${BASH_SOURCE[0]}")"
fi

if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    C_RESET=$'\033[0m'
    C_GREEN=$'\033[1;32m'
    C_BLUE=$'\033[1;34m'
    C_RED=$'\033[1;31m'
    C_YELLOW=$'\033[1;33m'
    C_BOLD=$'\033[1m'
fi

log_info()    { printf '%s[INFO]%s %s\n'  "${C_BLUE:-}"   "${C_RESET:-}" "$1"; }
log_success() { printf '%s[OK]%s %s\n'    "${C_GREEN:-}"  "${C_RESET:-}" "$1"; }
log_warn()    { printf '%s[WARN]%s %s\n'  "${C_YELLOW:-}" "${C_RESET:-}" "$1"; }
log_error()   { printf '%s[ERROR]%s %s\n' "${C_RED:-}"    "${C_RESET:-}" "$1" >&2; }
die()         { log_error "$1"; exit "${2:-1}"; }

print_help() {
    cat <<HELP_EOF
${C_BOLD:-}Usage:${C_RESET:-} ${SCRIPT_NAME} [OPTIONS]

  --auto, -a         Auto-detect RAM size and set dynamic THP profile (default)
  --aggressive, -A   Force >=32GB class "Performance" THP allocation (Looser limits, 4096 scan)
  --standard, -S     Force <32GB class "Strict RAM Savings" THP allocation (Tight limits, 1024 scan)
  --dry-run, -n      Print the generated systemd-tmpfiles config and exit
  --help, -h         Show this help menu
HELP_EOF
}

usage_error() { log_error "$1"; print_help >&2; exit 2; }

MODE="AUTO"
declare -i DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --auto|-a)       MODE="AUTO"; shift ;;
        --aggressive|-A) MODE="AGGRESSIVE"; shift ;;
        --standard|-S)   MODE="STANDARD"; shift ;;
        --dry-run|-n)    DRY_RUN=1; shift ;;
        --help|-h)       print_help; exit 0 ;;
        *)               usage_error "Unknown argument: $1" ;;
    esac
done

if [[ $EUID -ne 0 && $DRY_RUN -eq 0 ]]; then
    [[ -n "$SELF_PATH" ]] || die "Cannot self-escalate from piped stdin. Please re-run with sudo directly."
    command -v sudo >/dev/null 2>&1 || die "'sudo' is not available."
    log_info "Root privileges required. Escalating..."
    exec sudo -- /usr/bin/bash "$SELF_PATH" "${orig_args[@]}"
fi

declare -i SYSTEM_RAM_KB=0
declare -i SYSTEM_RAM_GB=0

if [[ $(< /proc/meminfo) =~ MemTotal:[[:space:]]+([0-9]+) ]]; then
    SYSTEM_RAM_KB=$(( BASH_REMATCH[1] ))
    SYSTEM_RAM_GB=$(( SYSTEM_RAM_KB / 1048576 ))
else
    die "FATAL: Could not parse /proc/meminfo."
fi

declare -i THRESHOLD_KB=29360128  # 28 GiB cutoff for >=32GB class
declare -i IS_PERF_MODE=0

declare -i EXPECTED_MAX_PTES
declare -i EXPECTED_MAX_PTES_SWAP
declare -i EXPECTED_SCAN_SLEEP
declare -i EXPECTED_PAGES_TO_SCAN
readonly EXPECTED_ALLOC_SLEEP=60000
readonly EXPECTED_KHUGEPAGED_DEFRAG=1

# Unified 4-Tier THP Demarcation
# S:  < 7 GiB       -> max_ptes_none = 128 (25% padding allowed, balanced baseline)
# M:  7 - < 14 GiB  -> max_ptes_none = 256 (50% padding allowed)
# L:  14 - < 28 GiB -> max_ptes_none = 450 (aggressive collapse for 16-24GB)
# XL: >= 28 GiB     -> max_ptes_none = 450 (aggressive collapse for >=32GB)

if [[ "$MODE" == "AGGRESSIVE" ]] || { [[ "$MODE" == "AUTO" ]] && (( SYSTEM_RAM_KB >= THRESHOLD_KB )); }; then
    IS_PERF_MODE=1
    EXPECTED_MODE="PERFORMANCE_LEAN (>=32GB class)"
    EXPECTED_MAX_PTES=450               # Aggressive collapse for large memory
    EXPECTED_MAX_PTES_SWAP=64           # Default swap-in allowance
    EXPECTED_SCAN_SLEEP=15000
    EXPECTED_PAGES_TO_SCAN=4096
elif (( SYSTEM_RAM_KB >= 14680064 )); then
    IS_PERF_MODE=1
    EXPECTED_MODE="BALANCED_PERFORMANCE (16-24GB class)"
    EXPECTED_MAX_PTES=450               # Aggressive collapse for 16-24GB
    EXPECTED_MAX_PTES_SWAP=0            # Forbid swapping pages back IN from ZRAM
    EXPECTED_SCAN_SLEEP=30000
    EXPECTED_PAGES_TO_SCAN=2048
elif (( SYSTEM_RAM_KB >= 7340032 )); then
    IS_PERF_MODE=0
    EXPECTED_MODE="DYNAMIC_EFFICIENCY (8-12GB class)"
    EXPECTED_MAX_PTES=256               # 50% threshold collapse
    EXPECTED_MAX_PTES_SWAP=0            # Forbid swapping pages back IN from ZRAM
    EXPECTED_SCAN_SLEEP=60000
    EXPECTED_PAGES_TO_SCAN=1024
else
    IS_PERF_MODE=0
    EXPECTED_MODE="COMPACT_EFFICIENCY (<8GB class)"
    EXPECTED_MAX_PTES=128               # Base 128 threshold (25% padding allowed)
    EXPECTED_MAX_PTES_SWAP=0            # Forbid swapping pages back IN from ZRAM
    EXPECTED_SCAN_SLEEP=60000
    EXPECTED_PAGES_TO_SCAN=1024
fi

readonly EXPECTED_ENABLED="madvise"
readonly EXPECTED_DEFRAG="defer+madvise"
readonly EXPECTED_SHMEM="within_size"

if [[ ! -d "$THP_BASE_DIR" && -d "/sys/kernel/mm" ]]; then
    die "FATAL: Transparent HugePages (THP) are not supported or disabled in this kernel."
fi

log_info "Initializing Multi-Size THP Optimizer..."
log_info "Detected System RAM: ${C_BOLD:-}${SYSTEM_RAM_GB} GB${C_RESET:-} (${SYSTEM_RAM_KB} KiB)"

if [[ "$MODE" != "AUTO" ]]; then
    log_warn "Manual Override Engaged: Profile forced to ${C_BOLD:-}${EXPECTED_MODE}${C_RESET:-}"
fi

tmpfile="$(umask 077 && mktemp)"
trap 'rm -f "${tmpfile:-}"' EXIT

cat > "$tmpfile" <<CONF_EOF
# Managed by ${SCRIPT_NAME}
# Scope: Transparent HugePages (mTHP) systemd-tmpfiles initialization
# Target: Kernel 7.2+ / systemd 261+ / Arch Linux
# Profile: ${EXPECTED_MODE} | Detected RAM: ${SYSTEM_RAM_GB}GB
# Docs: https://docs.kernel.org/admin-guide/mm/transhuge.html

# --- GLOBAL THP CONTROLS ---
w- /sys/kernel/mm/transparent_hugepage/enabled - - - - ${EXPECTED_ENABLED}
w- /sys/kernel/mm/transparent_hugepage/defrag - - - - ${EXPECTED_DEFRAG}
w- /sys/kernel/mm/transparent_hugepage/shmem_enabled - - - - ${EXPECTED_SHMEM}

# --- GLOBAL MEMORY EFFICIENCY & SHRINKER FLAGS ---
w- /sys/kernel/mm/transparent_hugepage/use_zero_page - - - - 1
w- /sys/kernel/mm/transparent_hugepage/shrink_underused - - - - 1

# --- KHUGEPAGED DAEMON TUNING ---
w- /sys/kernel/mm/transparent_hugepage/khugepaged/max_ptes_none - - - - ${EXPECTED_MAX_PTES}
w- /sys/kernel/mm/transparent_hugepage/khugepaged/max_ptes_swap - - - - ${EXPECTED_MAX_PTES_SWAP}
w- /sys/kernel/mm/transparent_hugepage/khugepaged/scan_sleep_millisecs - - - - ${EXPECTED_SCAN_SLEEP}
w- /sys/kernel/mm/transparent_hugepage/khugepaged/pages_to_scan - - - - ${EXPECTED_PAGES_TO_SCAN}
w- /sys/kernel/mm/transparent_hugepage/khugepaged/defrag - - - - ${EXPECTED_KHUGEPAGED_DEFRAG}
w- /sys/kernel/mm/transparent_hugepage/khugepaged/alloc_sleep_millisecs - - - - ${EXPECTED_ALLOC_SLEEP}

# --- MULTI-SIZE THP (mTHP) TIER DEFINITIONS ---
CONF_EOF

detected_sizes=()
for size_dir in "${THP_BASE_DIR}"/hugepages-*kB; do
    if [[ -d "$size_dir" ]]; then
        basename_dir="${size_dir##*/}"
        size_kb="${basename_dir#hugepages-}"
        size_kb="${size_kb%kB}"
        detected_sizes+=("$((size_kb))")
    fi
done

if (( ${#detected_sizes[@]} == 0 )); then
    log_info "THP hardware sysfs not populated (offline/chroot). Using standard x86_64 mTHP orders."
    detected_sizes=(16 32 64 128 256 512 1024 2048)
fi

for sz in "${detected_sizes[@]}"; do
    if (( sz < 16 )); then
        continue
    fi

    target_enabled="never"
    target_shmem="never"

    if (( IS_PERF_MODE == 1 )); then
        if (( sz == 64 || sz == 128 || sz == 2048 )); then
            target_enabled="madvise"
            target_shmem="inherit"
        fi
    else
        if (( sz == 64 || sz == 2048 )); then
            target_enabled="madvise"
            target_shmem="inherit"
        fi
    fi

    size_dir="${THP_BASE_DIR}/hugepages-${sz}kB"
    {
        echo ""
        echo "# mTHP Size: ${sz}kB"
        if [[ ! -d "$THP_BASE_DIR" || -f "${size_dir}/enabled" ]]; then
            echo "w- /sys/kernel/mm/transparent_hugepage/hugepages-${sz}kB/enabled - - - - ${target_enabled}"
        fi
        if [[ ! -d "$THP_BASE_DIR" || -f "${size_dir}/shmem_enabled" ]]; then
            echo "w- /sys/kernel/mm/transparent_hugepage/hugepages-${sz}kB/shmem_enabled - - - - ${target_shmem}"
        fi
    } >> "$tmpfile"
done

if (( DRY_RUN == 1 )); then
    log_info "DRY RUN EXECUTED. Generated configuration for ${CONFIG_FILE}:"
    cat "$tmpfile"
    exit 0
fi

if [[ -f "$CONFIG_FILE" ]] && cmp -s "$tmpfile" "$CONFIG_FILE"; then
    log_info "Configuration file matches desired state. Preserving existing file."
else
    install -Dm0644 "$tmpfile" "$CONFIG_FILE"
    log_success "Configuration successfully written to ${CONFIG_FILE}"
fi

log_info "Applying tmpfiles.d configuration to live sysfs..."
tmpfiles_output=""
if ! tmpfiles_output="$(systemd-tmpfiles --create "$CONFIG_FILE" 2>&1)"; then
    log_warn "systemd-tmpfiles applied with diagnostic messages:"
    printf '%s\n' "$tmpfiles_output"
fi

if [[ ! -d "$THP_BASE_DIR" ]]; then
    log_warn "Live sysfs unavailable (offline/chroot). Skipping live verification."
    exit 0
fi

actual_enabled="$(< "${THP_BASE_DIR}/enabled")"
actual_defrag="$(< "${THP_BASE_DIR}/defrag")"
actual_shmem="$(< "${THP_BASE_DIR}/shmem_enabled")"
actual_ptes="$(< "${THP_BASE_DIR}/khugepaged/max_ptes_none")"
actual_ptes_swap="$(< "${THP_BASE_DIR}/khugepaged/max_ptes_swap")"
actual_scan_sleep="$(< "${THP_BASE_DIR}/khugepaged/scan_sleep_millisecs")"
actual_pages_to_scan="$(< "${THP_BASE_DIR}/khugepaged/pages_to_scan")"

[[ "$actual_enabled" == *"[$EXPECTED_ENABLED]"* ]] || die "Verification failed: THP 'enabled' is '${actual_enabled}', expected '[${EXPECTED_ENABLED}]'."
[[ "$actual_defrag" == *"[$EXPECTED_DEFRAG]"* ]]   || die "Verification failed: THP 'defrag' is '${actual_defrag}', expected '[${EXPECTED_DEFRAG}]'."
[[ "$actual_shmem" == *"[$EXPECTED_SHMEM]"* ]]     || die "Verification failed: THP 'shmem_enabled' is '${actual_shmem}', expected '[${EXPECTED_SHMEM}]'."
[[ "$actual_ptes" == "$EXPECTED_MAX_PTES" ]]       || die "Verification failed: 'max_ptes_none' is '${actual_ptes}', expected '${EXPECTED_MAX_PTES}'."
[[ "$actual_ptes_swap" == "$EXPECTED_MAX_PTES_SWAP" ]] || die "Verification failed: 'max_ptes_swap' is '${actual_ptes_swap}', expected '${EXPECTED_MAX_PTES_SWAP}'."
[[ "$actual_scan_sleep" == "$EXPECTED_SCAN_SLEEP" ]] || die "Verification failed: 'scan_sleep_millisecs' is '${actual_scan_sleep}', expected '${EXPECTED_SCAN_SLEEP}'."
[[ "$actual_pages_to_scan" == "$EXPECTED_PAGES_TO_SCAN" ]] || die "Verification failed: 'pages_to_scan' is '${actual_pages_to_scan}', expected '${EXPECTED_PAGES_TO_SCAN}'."

if [[ -f "${THP_BASE_DIR}/use_zero_page" ]]; then
    [[ "$(< "${THP_BASE_DIR}/use_zero_page")" == "1" ]] || die "Verification failed: 'use_zero_page' is not 1."
fi

if [[ -f "${THP_BASE_DIR}/shrink_underused" ]]; then
    [[ "$(< "${THP_BASE_DIR}/shrink_underused")" == "1" ]] || die "Verification failed: 'shrink_underused' is not 1."
fi

if [[ -f "${THP_BASE_DIR}/khugepaged/defrag" ]]; then
    [[ "$(< "${THP_BASE_DIR}/khugepaged/defrag")" == "1" ]] || die "Verification failed: 'khugepaged/defrag' is not 1."
fi

if [[ -f "${THP_BASE_DIR}/khugepaged/alloc_sleep_millisecs" ]]; then
    [[ "$(< "${THP_BASE_DIR}/khugepaged/alloc_sleep_millisecs")" == "$EXPECTED_ALLOC_SLEEP" ]] || die "Verification failed: 'alloc_sleep_millisecs' is not $EXPECTED_ALLOC_SLEEP."
fi

for sz in "${detected_sizes[@]}"; do
    (( sz < 16 )) && continue
    size_dir="${THP_BASE_DIR}/hugepages-${sz}kB"
    [[ -d "$size_dir" ]] || continue

    target_enabled="never"
    target_shmem="never"

    if (( IS_PERF_MODE == 1 )); then
        if (( sz == 64 || sz == 128 || sz == 2048 )); then
            target_enabled="madvise"
            target_shmem="inherit"
        fi
    else
        if (( sz == 64 || sz == 2048 )); then
            target_enabled="madvise"
            target_shmem="inherit"
        fi
    fi

    if [[ -f "${size_dir}/enabled" ]]; then
        actual="$(< "${size_dir}/enabled")"
        if [[ "$actual" != *"[$target_enabled]"* && "$actual" != "$target_enabled" ]]; then
            die "Verification failed: mTHP ${sz}kB 'enabled' is '${actual}', expected '[${target_enabled}]'."
        fi
    fi

    if [[ -f "${size_dir}/shmem_enabled" ]]; then
        actual="$(< "${size_dir}/shmem_enabled")"
        if [[ "$actual" != *"[$target_shmem]"* && "$actual" != "$target_shmem" ]]; then
            die "Verification failed: mTHP ${sz}kB 'shmem_enabled' is '${actual}', expected '[${target_shmem}]'."
        fi
    fi
done

log_success "Verified live sysfs kernel values:"
log_success "  enabled = [${EXPECTED_ENABLED}]"
log_success "  defrag = [${EXPECTED_DEFRAG}]"
log_success "  shmem_enabled = [${EXPECTED_SHMEM}]"
log_success "  max_ptes_none = ${actual_ptes} (128=compact, 256=balanced, 450=perf collapse)"
log_success "  max_ptes_swap = ${actual_ptes_swap} (0=prevent swap-in uncompress)"
log_success "  scan_sleep_millisecs = ${actual_scan_sleep} (idle sleep)"
log_success "  pages_to_scan = ${actual_pages_to_scan}"
log_success "  use_zero_page = 1, shrink_underused = 1, khugepaged/defrag = 1"
log_success "  Active Profile: [${C_BOLD:-}${EXPECTED_MODE}${C_RESET:-}]"

exit 0

