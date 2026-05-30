#!/usr/bin/env bash
# =============================================================================
# Elite Arch Linux systemd-oomd & UWSM Optimizer
# Target: Arch Linux Cutting-Edge (systemd 255+, Bash 5.3+)
# Scope: Platinum Grade. Arms oomd with surgical kill policies, shields Hyprland.
# =============================================================================

set -euo pipefail

readonly SCRIPT_NAME="${0##*/}"

# --- Strict Path Resolution ---
readonly SELF_PATH="$(realpath -e -- "${BASH_SOURCE[0]}")"

# --- Target Configurations ---
readonly OOMD_DIR="/etc/systemd/oomd.conf.d"
readonly OOMD_CONF="${OOMD_DIR}/99-zram-tuning.conf"

readonly USER_SVC_DIR="/etc/systemd/system/user@.service.d"
readonly USER_SVC_CONF="${USER_SVC_DIR}/99-oomd-kill-policy.conf"

readonly USER_SLICE_DIR="/etc/systemd/user/session.slice.d"
readonly USER_SLICE_CONF="${USER_SLICE_DIR}/99-oomd-avoid.conf"

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
    command -v sudo >/dev/null 2>&1 || die "'sudo' is not available."
    log_info "Root privileges required. Escalating..."
    exec sudo -- /usr/bin/bash "$SELF_PATH" "$@"
fi

log_info "Initializing Platinum systemd-oomd & UWSM Optimizer..."

# --- 3. Temp File Generation ---
tmp_oomd="$(umask 077 && mktemp)"
tmp_user_svc="$(umask 077 && mktemp)"
tmp_session_slice="$(umask 077 && mktemp)"
trap 'rm -f "$tmp_oomd" "$tmp_user_svc" "$tmp_session_slice"' EXIT

# A. Global OOMD Limits (The Hair-Trigger)
cat > "$tmp_oomd" <<EOF
# Managed by ${SCRIPT_NAME}
[OOM]
SwapUsedLimit=90%
DefaultMemoryPressureLimit=60%
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

# C. User Session Slice Policy (The Shield)
cat > "$tmp_session_slice" <<EOF
# Managed by ${SCRIPT_NAME}
[Slice]
# Instructs systemd-oomd to heavily bias away from killing the desktop environment (Hyprland).
ManagedOOMPreference=avoid
EOF

# --- 4. Dry Run Check ---
if (( DRY_RUN == 1 )); then
    log_info "DRY RUN EXECUTED. Would generate the following configurations:"
    echo -e "\n${C_BOLD}[ ${OOMD_CONF} ]${C_RESET}"
    cat "$tmp_oomd"
    echo -e "\n${C_BOLD}[ ${USER_SVC_CONF} ]${C_RESET}"
    cat "$tmp_user_svc"
    echo -e "\n${C_BOLD}[ ${USER_SLICE_CONF} ]${C_RESET}"
    cat "$tmp_session_slice"
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

if (( CHANGED == 0 )); then
    log_success "No changes required. Existing systemd-oomd configuration is already optimal."
else
    log_info "Reloading systemd daemon to ingest new policies..."
    systemctl daemon-reload
    
    log_info "Reloading active user managers to ingest session shield..."
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
        fi
    done
    
    if systemctl -q is-active systemd-oomd.service >/dev/null 2>&1; then
        log_info "Restarting active systemd-oomd to apply new thresholds..."
        systemctl restart systemd-oomd.service
    fi
fi

# --- 6. Enable and Start ---
if ! systemctl -q is-active systemd-oomd.service >/dev/null 2>&1; then
    log_info "Enabling and starting systemd-oomd.service..."
    systemctl enable --now systemd-oomd.service || die "Failed to start systemd-oomd."
    log_success "systemd-oomd is now armed and active."
else
    log_success "systemd-oomd is already running and fully armed."
fi

exit 0
