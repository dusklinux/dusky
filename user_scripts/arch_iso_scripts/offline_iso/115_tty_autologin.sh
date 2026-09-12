#!/usr/bin/env bash
#d: Enable or disable TTY autologin

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
MODE_STATUS=false
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
            -s|--status)     MODE_STATUS=true ;;
            -u|--user)       TARGET_USER_OVERRIDE="$2"; shift ;;
            --_confirmed)    CONFIRMED=true ;; # Internal flag for sudo escalation
            -h|--help)
                printf "Usage: %s [OPTIONS]\n" "${0##*/}"
                printf "Options:\n"
                printf "  -a, --auto        Run non-interactively (skip prompts)\n"
                printf "  -r, --revert      Revert autologin and restore standard TTY/Greeter\n"
                printf "  -s, --status      Check current autologin status (outputs 'true' or 'false')\n"
                printf "  -u, --user <name> Explicitly set target user (Overrides auto-detection)\n"
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

# Check if SDDM is installed
sddm_is_installed() {
    [[ -f "/usr/lib/systemd/system/sddm.service" ]] || systemctl list-unit-files sddm.service &>/dev/null
}

# Check if greetd is installed
greetd_is_installed() {
    [[ -f "/usr/lib/systemd/system/greetd.service" ]] || systemctl list-unit-files greetd.service &>/dev/null
}

# Check if any display manager / greeter is currently enabled
dm_is_enabled() {
    local -a dms=("greetd.service" "sddm.service" "display-manager.service" "gdm.service" "lightdm.service" "lxdm.service" "ly.service")
    for dm in "${dms[@]}"; do
        if systemctl is-enabled --quiet "${dm}" 2>/dev/null; then
            return 0
        fi
    done
    return 1
}

# Check if the override file exists and configures autologin
autologin_override_is_active() {
    [[ -f "${OVERRIDE_FILE}" ]] || return 1
    grep -qE '^[[:space:]]*ExecStart=.*--autologin' "${OVERRIDE_FILE}" 2>/dev/null || return 1
    return 0
}

# Determine overall TTY1 autologin status
# Autologin is active ONLY IF no display manager is enabled AND the getty drop-in exists
is_autologin_active() {
    if dm_is_enabled; then
        return 1
    fi
    autologin_override_is_active
}

# Determine if systemd is the active init system for THIS root namespace.
is_systemd_active() {
    local pid1_comm pid1_root_inode our_root_inode

    pid1_comm=$(cat /proc/1/comm 2>/dev/null) || return 1
    [[ "${pid1_comm}" == "systemd" ]] || return 1

    pid1_root_inode=$(stat -Lc %i /proc/1/root 2>/dev/null) || return 1
    our_root_inode=$(stat -c %i /              2>/dev/null) || return 1

    [[ "${pid1_root_inode}" == "${our_root_inode}" ]]
}

# --- Helpers ---
sync_state_file() {
    local user="$1"
    local state="$2"
    local user_home

    # Reliably query the NSS database for the actual home directory
    user_home=$(getent passwd "${user}" | cut -d: -f6)
    if [[ -z "${user_home}" ]]; then
        log_error "Could not determine home directory for user: ${user}"
        return 1
    fi

    local state_dir="${user_home}/.config/dusky/settings"
    local state_file="${state_dir}/auto_login_tty"

    # Avoid redundant writes if already in desired state
    if [[ -f "${state_file}" ]] && [[ "$(<"${state_file}")" == "${state}" ]]; then
        return 0
    fi

    if [[ "${EUID}" -ne 0 && "${USER:-$(id -un)}" == "${user}" ]]; then
        mkdir -p "${state_dir}"
        printf "%s\n" "${state}" > "${state_file}"
    elif [[ "${EUID}" -eq 0 ]]; then
        if command -v runuser &>/dev/null; then
            runuser -u "${user}" -- mkdir -p "${state_dir}"
            runuser -u "${user}" -- sh -c "printf '%s\n' '${state}' > '${state_file}'"
        else
            su -s /bin/bash "${user}" -c "mkdir -p '${state_dir}' && printf '%s\n' '${state}' > '${state_file}'"
        fi
    else
        return 1
    fi

    log_info "Dusky state synced: ${state_file} -> [${state}]"
}

# --- Interactivity ---
prompt_user() {
    local action="$1"
    local target="$2"

    [[ "${MODE_AUTO}" == true || "${CONFIRMED}" == true ]] && return 0

    printf "\n${YELLOW}Arch Linux TTY1 Autologin Manager${NC}\n"

    if [[ "${action}" == "setup" ]]; then
        printf "Action: ${GREEN}ENABLE${NC} autologin for user: ${GREEN}%s${NC}\n" "${target}"
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

    local changed=false

    # Disable conflicting display managers
    local -a dms=("greetd.service" "sddm.service" "display-manager.service" "gdm.service" "lightdm.service" "lxdm.service" "ly.service")
    for dm in "${dms[@]}"; do
        if systemctl is-enabled --quiet "${dm}" 2>/dev/null; then
            log_info "Disabling ${dm}..."
            systemctl disable "${dm}" --quiet 2>/dev/null || true
            changed=true
            log_success "${dm} disabled."
        fi
    done

    local expected_exec="ExecStart=-/usr/bin/agetty --autologin ${user} --noclear --noissue %I \$TERM"
    if [[ -f "${OVERRIDE_FILE}" ]] && grep -qF -- "${expected_exec}" "${OVERRIDE_FILE}"; then
        if [[ "${changed}" == true ]] && is_systemd_active; then
            systemctl daemon-reload
            log_info "systemd daemon reloaded."
        fi
        sync_state_file "${user}" "true"
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
    log_success "Autologin configured successfully for ${user}."
}

do_revert() {
    local user="$1"
    log_info "Reverting TTY1 autologin configuration for ${user}..."
    local changed=false

    if [[ -f "${OVERRIDE_FILE}" ]]; then
        rm -f "${OVERRIDE_FILE}"
        rmdir --ignore-fail-on-non-empty "${SYSTEMD_DIR}" 2>/dev/null || true
        if is_systemd_active; then
            systemctl daemon-reload
            log_info "systemd daemon reloaded."
        else
            log_info "Non-live environment detected; skipping daemon-reload."
        fi
        changed=true
        log_success "Removed autologin drop-in override for ${SYSTEMD_UNIT}."
    fi

    if greetd_is_installed && ! systemctl is-enabled --quiet greetd.service 2>/dev/null; then
        log_info "Re-enabling greetd..."
        systemctl enable greetd.service --quiet 2>/dev/null || true
        changed=true
        log_success "greetd enabled."
    elif sddm_is_installed && ! systemctl is-enabled --quiet sddm.service 2>/dev/null; then
        log_info "Re-enabling SDDM..."
        systemctl enable sddm.service --quiet 2>/dev/null || true
        changed=true
        log_success "SDDM enabled."
    elif systemctl list-unit-files display-manager.service &>/dev/null && ! systemctl is-enabled --quiet display-manager.service 2>/dev/null; then
        log_info "Re-enabling display-manager..."
        systemctl enable display-manager.service --quiet 2>/dev/null || true
        changed=true
        log_success "display-manager enabled."
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

    # 1. Determine Target Context & Execution Mode
    local target_user=""

    if [[ -n "${TARGET_USER_OVERRIDE}" ]]; then
        target_user="${TARGET_USER_OVERRIDE}"
        MODE_AUTO=true # Explicitly targeted, bypass y/n prompt
    elif [[ "${EUID}" -eq 0 && -n "${SUDO_USER:-}" ]]; then
        target_user="${SUDO_USER}"
        MODE_AUTO=true # Live system via sudo, bypass y/n prompt
    elif [[ "${EUID}" -ne 0 ]]; then
        target_user="${USER:-$(id -un)}"
        MODE_AUTO=true # Live system normal user, bypass y/n prompt
    else
        # Raw root shell (e.g., arch-chroot)
        local -a available_users=()
        mapfile -t available_users < <(awk -F: '$3 >= 1000 && $3 < 65534 {print $1}' /etc/passwd)

        if [[ ${#available_users[@]} -eq 0 ]]; then
            log_error "No standard human users (UID >= 1000) identified on this system."
            log_error "Create a user first, or specify one explicitly with '-u <username>'."
            exit 1
        elif [[ ${#available_users[@]} -eq 1 ]]; then
            target_user="${available_users[0]}"
            MODE_AUTO=true # Exactly one user found, silent execution
        else
            # Multiple users found in chroot
            if [[ "${MODE_AUTO}" == true || "${CONFIRMED}" == true || "${MODE_STATUS}" == true ]]; then
                target_user="${available_users[0]}"
                [[ "${MODE_STATUS}" != true ]] && log_warn "Multiple users detected in --auto mode. Autonomously defaulting to primary user: ${target_user}"
            else
                printf "\n${YELLOW}Multiple standard users detected.${NC}\n"
                PS3="Select the target user for TTY1 Autologin (1-${#available_users[@]}): "
                select choice in "${available_users[@]}"; do
                    if [[ -n "${choice}" ]]; then
                        target_user="${choice}"
                        break
                    else
                        log_warn "Invalid selection. Please choose a valid number."
                    fi
                done
                MODE_AUTO=true # User selected, bypass subsequent y/N prompt
            fi
        fi
    fi

    # 2. Validate User Existence
    if ! id -u "${target_user}" &>/dev/null; then
        log_error "User '${target_user}' does not exist on this system."
        exit 1
    fi

    # 3. Status Query Mode (Non-privileged, non-interactive, instant exit)
    if [[ "${MODE_STATUS}" == true ]]; then
        if is_autologin_active; then
            printf "true\n"
            sync_state_file "${target_user}" "true" &>/dev/null || true
        else
            printf "false\n"
            sync_state_file "${target_user}" "false" &>/dev/null || true
        fi
        exit 0
    fi

    # 4. State Resolution & Prompting (Will naturally skip due to MODE_AUTO=true in all valid paths)
    local action_type="setup"
    [[ "${MODE_REVERT}" == true ]] && action_type="revert"
    prompt_user "${action_type}" "${target_user}"

    # 5. Privilege Escalation
    if [[ "${EUID}" -ne 0 ]]; then
        log_info "Escalating privileges..."

        if [[ ! -f "$0" ]] || [[ ! -r "$0" ]]; then
            log_error "Cannot re-execute: script path '$0' is not a regular readable file."
            exit 1
        fi

        local exec_args=("--_confirmed" "-u" "${target_user}")
        [[ "${MODE_AUTO}" == true ]] && exec_args+=("--auto")
        [[ "${MODE_REVERT}" == true ]] && exec_args+=("--revert")

        exec sudo "$0" "${exec_args[@]}"
    fi

    # 6. Execution Routine
    if [[ "${MODE_REVERT}" == true ]]; then
        do_revert "${target_user}"
    else
        do_setup "${target_user}"
    fi
}

main "$@"
