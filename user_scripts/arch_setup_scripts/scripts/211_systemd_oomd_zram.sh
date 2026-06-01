#!/usr/bin/env bash
# =============================================================================
# Elite Arch Linux systemd-oomd & UWSM Optimizer
# Target: Arch Linux Cutting-Edge (systemd 255+, Bash 5.3+)
# Scope: Platinum Grade. Arms oomd with surgical kill policies, shields Hyprland.
# Priority: Recalibrated for aggressive ZRAM. 10s kill-switch prevents system hangs.
# Updates: Added Search & Destroy for competing OOM daemons to prevent policy sabotage.
# =============================================================================

set -euo pipefail

readonly SCRIPT_NAME="${0##*/}"
readonly SELF_PATH="$(realpath -e -- "${BASH_SOURCE[0]}")"

# --- Target Configurations ---
readonly OOMD_DIR="/etc/systemd/oomd.conf.d"
readonly OOMD_CONF="${OOMD_DIR}/99-zram-tuning.conf"

readonly USER_SVC_DIR="/etc/systemd/system/user@.service.d"
readonly USER_SVC_CONF="${USER_SVC_DIR}/99-oomd-kill-policy.conf"

readonly USER_SLICE_DIR="/etc/systemd/user/session.slice.d"
readonly USER_SLICE_CONF="${USER_SLICE_DIR}/99-oomd-avoid.conf"

# Modern UWSM (Universal Wayland Session Manager) specific shield
readonly UWSM_SLICE_DIR="/etc/systemd/user/app-graphical-session.slice.d"
readonly UWSM_SLICE_CONF="${UWSM_SLICE_DIR}/99-oomd-avoid.conf"

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

log_info "Initializing Platinum systemd-oomd & UWSM Optimizer..."

# --- 2.5 Search & Destroy: Competing OOM Daemons ---
# Legacy daemons that read % free RAM will panic on ZRAM and destroy the session.
if (( DRY_RUN == 0 )); then
    log_info "Scanning for competing legacy OOM daemons..."
    for rogue_daemon in earlyoom nohang; do
        if systemctl is-enabled "$rogue_daemon" &>/dev/null || systemctl is-active "$rogue_daemon" &>/dev/null; then
            log_warn "Sabotage risk detected: '${rogue_daemon}' is active or enabled."
            systemctl disable --now "$rogue_daemon" >/dev/null 2>&1 || true
            systemctl mask "$rogue_daemon" >/dev/null 2>&1 || true
            log_success "Neutralized ${rogue_daemon}. systemd-oomd now has absolute authority."
        fi
    done
fi

# --- 3. Temp File Generation ---
tmp_oomd="$(umask 077 && mktemp)"
tmp_user_svc="$(umask 077 && mktemp)"
tmp_session_slice="$(umask 077 && mktemp)"
tmp_uwsm_slice="$(umask 077 && mktemp)"
trap 'rm -f "$tmp_oomd" "$tmp_user_svc" "$tmp_session_slice" "$tmp_uwsm_slice"' EXIT

# A. Global OOMD Limits (The Recalibrated Hair-Trigger)
cat > "$tmp_oomd" <<EOF
# Managed by ${SCRIPT_NAME}
[OOM]
# ZRAM Architecture: High swap usage is expected and desired.
# Pushed to 96% to prevent premature kills of healthy, heavily compressed systems.
SwapUsedLimit=96%

# Pressure Stall Information (PSI):
# Increased limit (70%) and duration (10s) to allow CPU time to heavily compress
# ZRAM memory during load spikes without triggering false-positive OOM kills or system hangs.
DefaultMemoryPressureLimit=70%
DefaultMemoryPressureDurationSec=10s
EOF

# B. User Service Policy (The Fangs)
cat > "$tmp_user_svc" <<EOF
# Managed by ${SCRIPT_NAME}
[Service]
# Grants systemd-oomd the authority to monitor and kill runaway apps in the user session.
ManagedOOMMemoryPressure=kill
ManagedOOMSwap=kill
EOF

# C. Desktop Session Shields (The Shield)
cat > "$tmp_session_slice" <<EOF
# Managed by ${SCRIPT_NAME}
[Slice]
# Instructs systemd-oomd to heavily bias away from killing the desktop environment.
ManagedOOMPreference=avoid
EOF

# Clone the shield for modern UWSM graphical sessions
cp "$tmp_session_slice" "$tmp_uwsm_slice"

# --- 4. Dry Run Check ---
if (( DRY_RUN == 1 )); then
    log_info "DRY RUN EXECUTED. Would generate the following configurations:"
    echo -e "\n${C_BOLD}[ ${OOMD_CONF} ]${C_RESET}"
    cat "$tmp_oomd"
    echo -e "\n${C_BOLD}[ ${USER_SVC_CONF} ]${C_RESET}"
    cat "$tmp_user_svc"
    echo -e "\n${C_BOLD}[ ${USER_SLICE_CONF} (Legacy) ]${C_RESET}"
    cat "$tmp_session_slice"
    echo -e "\n${C_BOLD}[ ${UWSM_SLICE_CONF} (Modern UWSM) ]${C_RESET}"
    cat "$tmp_uwsm_slice"
    exit 0
fi

# --- 5. Atomic Installation ---
declare -i CHANGED=0

install_file() {
    local src="$1" dest="$2" dir
    dir="$(dirname "$dest")"
    install -d -m 0755 "$dir"
    
    if [[ -f "$dest" ]] && cmp -s "$src" "$dest"; then
        log_info "${dest} is already up to date."
    else
        install -m 0644 "$src" "$dest"
        log_success "Updated ${dest}"
        CHANGED=1
    fi
}

install_file "$tmp_oomd" "$OOMD_CONF"
install_file "$tmp_user_svc" "$USER_SVC_CONF"
install_file "$tmp_session_slice" "$USER_SLICE_CONF"
install_file "$tmp_uwsm_slice" "$UWSM_SLICE_CONF"

if (( CHANGED == 0 )); then
    log_success "No changes required. Existing systemd-oomd configuration is already optimal."
else
    log_info "Reloading systemd daemon to ingest new policies..."
    systemctl daemon-reload || log_warn "Global daemon-reload failed. Continuing..."
    
    log_info "Reloading active user managers to ingest session shields..."
    declare -a uids=()
    while read -r line; do
        if [[ "$line" =~ user@([0-9]+)\.service ]]; then
            uids+=("${BASH_REMATCH[1]}")
        fi
    done < <(systemctl list-units --type=service --state=active --plain 'user@*.service' 2>/dev/null || true)

    for uid in "${uids[@]:-}"; do
        user="$(id -un "$uid" 2>/dev/null || true)"
        [[ -z "$user" ]] && continue
        
        if systemctl --user --machine="${user}@.host" daemon-reload >/dev/null 2>&1; then
            log_success "Reloaded user manager for ${user}."
        elif command -v runuser >/dev/null 2>&1 && [[ -S "/run/user/${uid}/bus" ]]; then
            if runuser -u "$user" -- env XDG_RUNTIME_DIR="/run/user/${uid}" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${uid}/bus" systemctl --user daemon-reload >/dev/null 2>&1; then
                log_success "Reloaded user manager for ${user} (via runuser)."
            fi
        fi || true 
    done
    
    if systemctl -q is-active systemd-oomd.service >/dev/null 2>&1; then
        log_info "Restarting active systemd-oomd to apply new thresholds..."
        systemctl restart systemd-oomd.service || log_warn "Failed to restart active systemd-oomd."
    fi
fi

# --- 6. Enable and Start ---
log_info "Enabling and starting systemd-oomd.service..."
systemctl enable systemd-oomd.service >/dev/null 2>&1 || log_warn "Failed to enable systemd-oomd."
systemctl start systemd-oomd.service >/dev/null 2>&1 || log_warn "Failed to start systemd-oomd."

if systemctl -q is-active systemd-oomd.service >/dev/null 2>&1; then
    log_success "systemd-oomd is fully armed, active, and shielding the Wayland session."
else
    log_warn "systemd-oomd is NOT active. You may need to reboot or check 'systemctl status systemd-oomd'."
fi

exit 0
