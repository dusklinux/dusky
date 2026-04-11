#!/usr/bin/env bash
# ==============================================================================
# Script Name: tty_autologin_manager.sh
# Description: Manages systemd TTY1 autologin for Arch Linux (Hyprland/UWSM).
#              Surgically idempotent, non-interactive capable, chroot-safe,
#              safe against sudo stripping, and maintains state for dusky.
# ==============================================================================

set -euo pipefail

# --- Constants & Styling ---
readonly SYSTEMD_UNIT="getty@tty1.service"
readonly SYSTEMD_DIR="/etc/systemd/system/${SYSTEMD_UNIT}.d"
readonly OVERRIDE_FILE="${SYSTEMD_DIR}/override.conf"

readonly RED=$'\033[0;31m'
readonly GREEN=$'\033[0;32m'
readonly BLUE=$'\033[0;34m'
readonly YELLOW=$'\033[1;33m'
readonly NC=$'\033[0m'

# --- State Variables ---
MODE_AUTO=false
MODE_REVERT=false
CONFIRMED=false
TARGET_USER_OVERRIDE=""

# --- Logging ---
log_info()    { printf "${BLUE}[INFO]${NC} %s\n" "$1"; }
log_success() { printf "${GREEN}[SUCCESS]${NC} %s\n" "$1"; }
log_warn()    { printf "${YELLOW}[WARN]${NC} %s\n" "$1"; }
log_error()   { printf "${RED}[ERROR]${NC} %s\n" "$1" >&2; }

# --- CLI Parsing ---
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -a|--auto)       MODE_AUTO=true ;;
            -r|--revert)     MODE_REVERT=true ;;
            -u|--user)       TARGET_USER_OVERRIDE="$2"; shift ;;
            --_confirmed)    CONFIRMED=true ;; # Internal flag for sudo escalation
            -h|--help)
                printf "Usage: %s [OPTIONS]\n" "${0##*/}"
                printf "Options:\n"
                printf "  -a, --auto        Run non-interactively (skip prompts)\n"
                printf "  -r, --revert      Revert autologin and restore standard TTY/SDDM\n"
                printf "  -u, --user <name> Explicitly set target user (Required in arch-chroot)\n"
                printf "  -h, --help        Show this help message\n"
                exit 0
                ;;
            *)
                log_error "Unknown argument: $1"
                exit 1
                ;;
        esac
        shift
    done
}

# --- Environment Detection ---

# Check if SDDM is installed by inspecting the unit file on disk.
# Direct filesystem inspection is more reliable than 'systemctl list-unit-files'
# as it carries no dbus dependency and behaves identically in chroot and live
# environments. On Arch Linux, pacman always installs unit files to
# /usr/lib/systemd/system/, making this path canonical.
sddm_is_installed() {
    [[ -f "/usr/lib/systemd/system/sddm.service" ]]
}

# Determine if systemd is the active init system for THIS root namespace.
#
# The naive check (is /proc/1/comm == "systemd"?) is insufficient because
# arch-chroot bind-mounts /proc from the host, making the host's PID 1 visible
# inside the chroot. This function resolves ambiguity by comparing the inode of
# PID 1's root directory (/proc/1/root) against our own root inode (/).
#
#   Live system:  PID 1's root inode == our root inode  → true  (systemd active)
#   arch-chroot:  PID 1's root inode != our root inode  → false (chroot env)
#   /proc absent: stat fails, || return 1 triggers      → false (safe default)
#   systemd-nspawn: inodes match within container       → true  (correct)
is_systemd_active() {
    local pid1_comm pid1_root_inode our_root_inode

    # Guard: is PID 1 even systemd?
    pid1_comm=$(cat /proc/1/comm 2>/dev/null) || return 1
    [[ "${pid1_comm}" == "systemd" ]] || return 1

    # Guard: does PID 1 share our root? (the chroot discriminator)
    pid1_root_inode=$(stat -Lc %i /proc/1/root 2>/dev/null) || return 1
    our_root_inode=$(stat -c %i /              2>/dev/null) || return 1

    [[ "${pid1_root_inode}" == "${our_root_inode}" ]]
}

# --- Helpers ---
sync_state_file() {
    local user="$1"
    local state="$2"
    local user_home
    local user_group

    # Reliably query the NSS database for the actual home directory and primary group
    user_home=$(getent passwd "${user}" | cut -d: -f6)
    user_group=$(id -gn "${user}")

    if [[ -z "${user_home}" ]]; then
        log_error "Could not determine home directory for user: ${user}"
        exit 1
    fi

    local config_dir="${user_home}/.config"
    local state_dir="${config_dir}/dusky/settings"
    local state_file="${state_dir}/auto_login_tty"

    # Chroot-safe directory creation and permission enforcement (no sudo dependency)
    if [[ ! -d "${config_dir}" ]]; then
        mkdir -p "${config_dir}"
        chown "${user}:${user_group}" "${config_dir}"
    fi

    mkdir -p "${state_dir}"
    echo "${state}" > "${state_file}"
    chown -R "${user}:${user_group}" "${config_dir}/dusky"

    log_info "Dusky state synced: ${state_file} -> [${state}]"
}

# --- Interactivity ---
prompt_user() {
    local action="$1"
    local target="$2"

    # Bypass prompts if in auto mode or if already confirmed pre-escalation
    [[ "${MODE_AUTO}" == true || "${CONFIRMED}" == true ]] && return 0

    printf "\n${YELLOW}Arch Linux TTY1 Autologin Manager${NC}\n"

    if [[ "${action}" == "setup" ]]; then
        printf "Action: ${GREEN}ENABLE${NC} autologin for user ${GREEN}%s${NC}\n" "${target}"
    else
        printf "Action: ${RED}REVERT${NC} autologin and restore default behavior.\n"
    fi

    read -r -p "Proceed? [y/N] " response
    if [[ ! "${response}" =~ ^[yY](es)?$ ]]; then
        log_info "Operation cancelled by user."
        exit 0
    fi
}

# --- Core Logic ---
do_setup() {
    local user="$1"
    log_info "Configuring TTY1 autologin for: ${user}"

    if sddm_is_installed && systemctl is-enabled --quiet sddm.service 2>/dev/null; then
        log_info "Disabling SDDM..."
        systemctl disable sddm.service --quiet 2>/dev/null || true
        log_success "SDDM disabled."
    fi

    # Exact string match idempotency check
    local expected_exec="ExecStart=-/usr/bin/agetty --autologin ${user} --noclear --noissue %I \$TERM"
    if [[ -f "${OVERRIDE_FILE}" ]] && grep -qF -- "${expected_exec}" "${OVERRIDE_FILE}"; then
        sync_state_file "${user}" "true" # Ensure state file is accurate even if systemd is already configured
        log_success "Autologin is already correctly configured for ${user}. Nothing to do."
        return 0
    fi

    mkdir -p "${SYSTEMD_DIR}"

    cat > "${OVERRIDE_FILE}" <<EOF
[Service]
ExecStart=
ExecStart=-/usr/bin/agetty --autologin ${user} --noclear --noissue %I \$TERM
EOF

    if is_systemd_active; then
        systemctl daemon-reload
        log_info "systemd daemon reloaded."
    else
        log_info "Non-live environment detected; skipping daemon-reload (will take effect on boot)."
    fi

    sync_state_file "${user}" "true"
    log_success "Autologin configured successfully."
}

do_revert() {
    local user="$1"
    log_info "Reverting TTY1 autologin configuration..."
    local changed=false

    # Surgical removal ensures we don't destroy other user drop-ins
    if [[ -f "${OVERRIDE_FILE}" ]]; then
        rm -f "${OVERRIDE_FILE}"
        # Only remove the directory if it's completely empty
        rmdir --ignore-fail-on-non-empty "${SYSTEMD_DIR}" 2>/dev/null || true
        if is_systemd_active; then
            systemctl daemon-reload
            log_info "systemd daemon reloaded."
        else
            log_info "Non-live environment detected; skipping daemon-reload (will take effect on boot)."
        fi
        changed=true
        log_success "Removed autologin drop-in override for ${SYSTEMD_UNIT}."
    fi

    if sddm_is_installed && ! systemctl is-enabled --quiet sddm.service 2>/dev/null; then
        log_info "Re-enabling SDDM..."
        systemctl enable sddm.service --quiet 2>/dev/null || true
        changed=true
        log_success "SDDM enabled."
    fi

    sync_state_file "${user}" "false"

    if [[ "${changed}" == true ]]; then
        log_success "Revert complete. Standard TTY login / Display Manager restored."
    else
        log_success "System already in default state. Nothing to revert."
    fi
}

# --- Entry Point ---
main() {
    parse_args "$@"

    # 1. Determine Target Context (Chroot & Live System safe)
    local target_user="${TARGET_USER_OVERRIDE}"

    if [[ -z "${target_user}" ]]; then
        if [[ "${EUID}" -eq 0 ]]; then
            target_user="${SUDO_USER:-}"
            if [[ -z "${target_user}" ]]; then
                log_error "Cannot determine target user from raw root shell (e.g., arch-chroot)."
                log_error "Execute with '-u <username>' to specify the user explicitly."
                exit 1
            fi
        else
            target_user="${USER}"
        fi
    fi

    # 2. Validate User Existence
    if ! id -u "${target_user}" &>/dev/null; then
        log_error "User '${target_user}' does not exist on this system."
        exit 1
    fi

    # 3. State Resolution & Prompting
    local action_type="setup"
    [[ "${MODE_REVERT}" == true ]] && action_type="revert"

    prompt_user "${action_type}" "${target_user}"

    # 4. Privilege Escalation
    if [[ "${EUID}" -ne 0 ]]; then
        log_info "Escalating privileges..."

        # Guard against exotic invocation methods (e.g. process substitution, piped input)
        if [[ ! -f "$0" ]] || [[ ! -r "$0" ]]; then
            log_error "Cannot re-execute: script path '$0' is not a regular readable file."
            log_error "Please save the script to disk and run it directly."
            exit 1
        fi

        # Build array payload to prevent word-splitting on re-execution
        local exec_args=("--_confirmed" "-u" "${target_user}")
        [[ "${MODE_AUTO}" == true ]] && exec_args+=("--auto")
        [[ "${MODE_REVERT}" == true ]] && exec_args+=("--revert")

        exec sudo "$0" "${exec_args[@]}"
    fi

    # 5. Execution Routine
    if [[ "${MODE_REVERT}" == true ]]; then
        do_revert "${target_user}"
    else
        do_setup "${target_user}"
    fi
}

main "$@"
