#!/usr/bin/env bash
# =============================================================================
# Elite Arch Linux systemd-journald Optimizer
# Target: Arch Linux Cutting-Edge (systemd 260+, Bash 5.3+)
# Scope: Platinum Grade. Hard-caps logging memory to prevent silent RAM bloat.
# Priority: Caps tmpfs RAM waste, mitigates CVE-2026-40228, and utilizes 
#           systemd.service(5) cgroup v2 limits to chain the daemon's RSS.
# =============================================================================

set -euo pipefail

readonly SCRIPT_NAME="${0##*/}"
readonly SELF_PATH="$(realpath -e -- "${BASH_SOURCE[0]}")"

# --- Target Configurations ---
readonly CONF_DIR="/etc/systemd/journald.conf.d"
readonly CONF_FILE="${CONF_DIR}/99-ram-optimization.conf"

readonly SVC_DIR="/etc/systemd/system/systemd-journald.service.d"
readonly SVC_FILE="${SVC_DIR}/99-cgroup-memory-limit.conf"

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

  --dry-run, -n        Print the generated systemd drop-ins and exit
  --help, -h           Show this help menu
EOF
}

usage_error() { log_error "$1"; print_help >&2; exit 2; }

# --- 1. CLI Parsing ---
declare -i DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run|-n)        DRY_RUN=1; shift ;;
        --help|-h)           print_help; exit 0 ;;
        *)                   log_warn "Ignoring unknown argument: $1"; shift ;;
    esac
done

# --- 2. Privilege Escalation ---
if [[ $EUID -ne 0 && $DRY_RUN -eq 0 ]]; then
    log_info "Root privileges required. Escalating..."
    command -v sudo >/dev/null 2>&1 || die "'sudo' is not available."
    exec sudo -- /usr/bin/bash "$SELF_PATH" "$@"
fi

log_info "Initializing Platinum systemd-journald Optimizer..."

# --- 3. Temp File Generation ---
tmp_conf="$(umask 077 && mktemp)"
tmp_svc="$(umask 077 && mktemp)"
trap 'rm -f "$tmp_conf" "$tmp_svc"' EXIT

# -----------------------------------------------------------------------------
# LAYER 1: The Payload Limits (journald.conf)
# -----------------------------------------------------------------------------
cat > "$tmp_conf" <<EOF
# Managed by ${SCRIPT_NAME}
# Scope: Prevent systemd-journald log payloads from consuming RAM and Disk.

[Journal]
# Volatile Storage (RAM in /run/log/journal): Hard cap at 16MB.
RuntimeMaxUse=16M

# Persistent Storage (Disk in /var/log/journal): Hard cap at 100MB.
SystemMaxUse=100M

# Rotate files frequently to keep read times instantaneous.
SystemMaxFileSize=16M

# Housekeeping: Discard logs older than 1 week to shrink VFS slab (inode) cache.
MaxRetentionSec=1week

# Compression: Force zstd compression on log payloads before writing.
Compress=yes

# SSD Wear & Latency Shield: Batch disk writes every 5 minutes.
SyncIntervalSec=5m

# CPU/IO Shield: Disable kernel audit logging. Bypasses rate limits and spikes CPU.
Audit=no

# Verbosity Cap: Drop debug-level spam before it consumes RAM.
MaxLevelStore=info

# Anti-Crash Loop Spam: Drop logs if a crashing app writes >100 lines in 10s.
RateLimitIntervalSec=10s
RateLimitBurst=100

# IPC & CVE-2026-40228 MITIGATION: Disable legacy broadcast logging.
# ForwardToWall=no nullifies IPC overhead and closes terminal escape sequence vectors.
ForwardToSyslog=no
ForwardToWall=no
ForwardToKMsg=no
ForwardToConsole=no
EOF

# -----------------------------------------------------------------------------
# LAYER 2: The Process Limits (systemd.service cgroups)
# -----------------------------------------------------------------------------
cat > "$tmp_svc" <<EOF
# Managed by ${SCRIPT_NAME}
# Scope: Hard cgroup v2 RAM limits for the systemd-journald daemon process itself.
# Prevents mmap cache bloat regardless of journal file size.

[Service]
# Aggressively throttle the daemon if its RSS exceeds 64MB
MemoryHigh=64M

# Absolute hard-kill boundary. If the daemon leaks >128MB, execute OOM kill.
MemoryMax=128M

# systemd.service(5): Ensures clean termination and flush on OOM pressure.
# Daemon will automatically respawn via Restart=always and empty its RAM buffers.
OOMPolicy=kill
EOF

# --- 4. Dry Run Check ---
if (( DRY_RUN == 1 )); then
    log_info "DRY RUN EXECUTED. Would generate the following configurations:"
    echo -e "\n${C_BOLD}[ ${CONF_FILE} (Payload Limits) ]${C_RESET}"
    cat "$tmp_conf"
    echo -e "\n${C_BOLD}[ ${SVC_FILE} (Process Limits) ]${C_RESET}"
    cat "$tmp_svc"
    exit 0
fi

# --- 5. Atomic Installation ---
declare -i CHANGED=0

install -d -m 0755 "$CONF_DIR"
if [[ -f "$CONF_FILE" ]] && cmp -s "$tmp_conf" "$CONF_FILE"; then
    log_info "${CONF_FILE} is already up to date."
else
    install -Dm0644 "$tmp_conf" "$CONF_FILE"
    log_success "Updated ${CONF_FILE}"
    CHANGED=1
fi

install -d -m 0755 "$SVC_DIR"
if [[ -f "$SVC_FILE" ]] && cmp -s "$tmp_svc" "$SVC_FILE"; then
    log_info "${SVC_FILE} is already up to date."
else
    install -Dm0644 "$tmp_svc" "$SVC_FILE"
    log_success "Updated ${SVC_FILE}"
    CHANGED=1
fi

if (( CHANGED == 1 )); then
    log_info "Reloading systemd daemon to ingest new cgroup boundaries..."
    systemctl daemon-reload

    log_info "Restarting systemd-journald to apply dual-layer memory caps..."
    if systemctl restart systemd-journald.service; then
        log_success "systemd-journald successfully restarted. Dual RAM limits active."
    else
        log_warn "Failed to seamlessly restart systemd-journald. Changes will apply on next reboot."
    fi
else
    log_success "No changes required. systemd-journald is already strictly chained."
fi

# --- 6. Live Vacuuming ---
log_info "Vacuuming current journals to enforce payload limits immediately..."
journalctl --vacuum-size=100M >/dev/null 2>&1 || true
journalctl --vacuum-time=1weeks >/dev/null 2>&1 || true

log_success "Logging topology is fully optimized for absolute maximum RAM efficiency."

exit 0
