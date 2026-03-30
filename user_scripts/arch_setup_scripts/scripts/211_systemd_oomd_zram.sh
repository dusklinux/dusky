#!/usr/bin/env bash
# systemd-oomd ZRAM & UWSM Integration Configurator
# -----------------------------------------------------------------------------
# Description:
#   Installs systemd-oomd defaults intended for high-usage ZRAM swap and adds
#   a user-manager session.slice preference that biases oomd away from killing
#   session infrastructure.
#
# Scope:
#   - This writes oomd.conf.d and systemd --user session.slice drop-ins.
#   - The session.slice override is global to all user managers on the system.
#   - ManagedOOMPreference=avoid is advisory, not an absolute exemption.
#   - This script does NOT enable systemd-oomd and does NOT add ManagedOOM*
#     kill policies to units that do not already have them.
# -----------------------------------------------------------------------------

set -euo pipefail

if [[ -t 1 || -t 2 ]]; then
    C_RESET=$'\033[0m'
    C_GREEN=$'\033[1;32m'
    C_BLUE=$'\033[1;34m'
    C_YELLOW=$'\033[1;33m'
    C_RED=$'\033[1;31m'
else
    C_RESET=''
    C_GREEN=''
    C_BLUE=''
    C_YELLOW=''
    C_RED=''
fi

log_info()    { printf '%b %s\n' "${C_BLUE}[INFO]${C_RESET}" "$1"; }
log_success() { printf '%b %s\n' "${C_GREEN}[SUCCESS]${C_RESET}" "$1"; }
log_warn()    { printf '%b %s\n' "${C_YELLOW}[WARN]${C_RESET}" "$1"; }
log_error()   { printf '%b %s\n' "${C_RED}[ERROR]${C_RESET}" "$1" >&2; }

readonly SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"

readonly OOMD_SERVICE="systemd-oomd.service"
readonly OOMD_DIR="/etc/systemd/oomd.conf.d"
readonly OOMD_CONF="${OOMD_DIR}/99-zram-tuning.conf"

readonly USER_SLICE_DIR="/etc/systemd/user/session.slice.d"
readonly USER_SLICE_CONF="${USER_SLICE_DIR}/99-session-slice-oom-preference.conf"

tmp_oomd=''
tmp_session_slice=''

cleanup() {
    [[ -n ${tmp_oomd:-} ]] && rm -f -- "$tmp_oomd"
    [[ -n ${tmp_session_slice:-} ]] && rm -f -- "$tmp_session_slice"
}

reload_one_user_manager() {
    local uid=$1
    local user=$2

    # First try the systemd-native machine syntax.
    if systemctl --user --machine="${user}@.host" daemon-reload >/dev/null 2>&1; then
        log_info "Reloaded systemd --user manager for ${user}."
        return 0
    fi

    # Fallback: talk to the user's bus directly if available.
    if command -v runuser >/dev/null 2>&1 && [[ -S "/run/user/${uid}/bus" ]]; then
        if runuser -u "$user" -- env \
            XDG_RUNTIME_DIR="/run/user/${uid}" \
            DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${uid}/bus" \
            systemctl --user daemon-reload >/dev/null 2>&1; then
            log_info "Reloaded systemd --user manager for ${user}."
            return 0
        fi
    fi

    return 1
}

reload_active_user_managers() {
    local any=0
    local failed=0
    local unit uid user

    while read -r unit _; do
        [[ -n ${unit:-} ]] || continue

        if [[ ! $unit =~ ^user@([0-9]+)\.service$ ]]; then
            continue
        fi

        uid="${BASH_REMATCH[1]}"
        user="$(getent passwd "$uid" 2>/dev/null | cut -d: -f1 || true)"

        if [[ -z ${user:-} ]]; then
            log_warn "Could not resolve username for active user manager UID ${uid}; skipping reload."
            failed=1
            continue
        fi

        any=1
        if ! reload_one_user_manager "$uid" "$user"; then
            log_warn "Could not reload the active user manager for ${user}. A logout/login or reboot may be required for session.slice changes to take effect."
            failed=1
        fi
    done < <(systemctl list-units --type=service --state=active --no-legend --plain 'user@*.service' 2>/dev/null || true)

    if (( any == 0 )); then
        log_info "No active user managers were found. session.slice changes will apply to future logins."
    elif (( failed == 0 )); then
        log_success "Reloaded all detected active user managers."
    fi
}

main() {
    trap cleanup EXIT

    if [[ $EUID -ne 0 ]]; then
        log_info "Script not run as root. Escalating privileges..."
        if ! command -v sudo >/dev/null 2>&1; then
            log_error "sudo is required for privilege escalation but was not found."
            exit 1
        fi

        if [[ $- == *x* ]]; then
            exec sudo -- bash -x -- "$SCRIPT_PATH" "$@"
        else
            exec sudo -- bash -- "$SCRIPT_PATH" "$@"
        fi
    fi

    local oomd_enabled=0
    local oomd_active=0
    local oomd_conf_changed=0
    local user_slice_changed=0
    local anything_changed=0

    if systemctl -q is-enabled "$OOMD_SERVICE" >/dev/null 2>&1; then
        oomd_enabled=1
    fi

    if systemctl -q is-active "$OOMD_SERVICE" >/dev/null 2>&1; then
        oomd_active=1
    fi

    if (( oomd_enabled == 0 && oomd_active == 0 )); then
        log_warn "systemd-oomd is neither enabled nor active on this system."
        log_info "Skipping oomd-specific tuning."
        exit 0
    fi

    log_info "systemd-oomd detected. Applying ZRAM-oriented oomd settings..."

    install -d -m 0755 -- "$OOMD_DIR"
    install -d -m 0755 -- "$USER_SLICE_DIR"

    tmp_oomd="$(mktemp "${OOMD_DIR}/.99-zram-tuning.tmp.XXXXXX")"
    cat > "$tmp_oomd" <<'EOF'
# Managed by Elite Arch Linux ZRAM Configurator
[OOM]
SwapUsedLimit=95%
DefaultMemoryPressureLimit=80%
DefaultMemoryPressureDurationSec=30
EOF
    chmod 0644 -- "$tmp_oomd"

    tmp_session_slice="$(mktemp "${USER_SLICE_DIR}/.99-session-slice-oom-preference.tmp.XXXXXX")"
    cat > "$tmp_session_slice" <<'EOF'
# Managed by Elite Arch Linux ZRAM Configurator
[Slice]
ManagedOOMPreference=avoid
EOF
    chmod 0644 -- "$tmp_session_slice"

    if [[ ! -f "$OOMD_CONF" ]] || ! cmp -s "$tmp_oomd" "$OOMD_CONF"; then
        mv -f -- "$tmp_oomd" "$OOMD_CONF"
        tmp_oomd=''
        oomd_conf_changed=1
        anything_changed=1
        log_success "Updated ${OOMD_CONF}"
    else
        rm -f -- "$tmp_oomd"
        tmp_oomd=''
        log_info "Global oomd configuration already matches desired state."
    fi

    if [[ ! -f "$USER_SLICE_CONF" ]] || ! cmp -s "$tmp_session_slice" "$USER_SLICE_CONF"; then
        mv -f -- "$tmp_session_slice" "$USER_SLICE_CONF"
        tmp_session_slice=''
        user_slice_changed=1
        anything_changed=1
        log_success "Updated ${USER_SLICE_CONF}"
    else
        rm -f -- "$tmp_session_slice"
        tmp_session_slice=''
        log_info "session.slice preference already matches desired state."
    fi

    if (( anything_changed == 0 )); then
        log_success "No changes required. Existing configuration already matches the desired state."
        exit 0
    fi

    if (( user_slice_changed == 1 )); then
        log_info "Reloading active user managers to pick up session.slice changes..."
        reload_active_user_managers
    fi

    # Restart oomd only if it is already active. This avoids starting the
    # service unexpectedly on systems where it is merely enabled or was stopped.
    if (( oomd_active == 1 )); then
        log_info "Restarting active ${OOMD_SERVICE} to apply changes..."
        systemctl restart "$OOMD_SERVICE"
        log_success "${OOMD_SERVICE} restarted successfully."
    else
        log_info "${OOMD_SERVICE} is enabled but not currently active; new settings will apply the next time it starts."
    fi

    log_success "Configuration update complete."
    log_info "ManagedOOMPreference=avoid biases oomd away from session.slice, but it is not an absolute kill-proof guarantee."
}

main "$@"
