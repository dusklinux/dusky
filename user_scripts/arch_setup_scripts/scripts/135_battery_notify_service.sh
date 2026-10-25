#!/usr/bin/env bash
#d: Install or uninstall the battery notification service

set -euo pipefail

# --- Styling & Colors ---
readonly RED=$'\033[0;31m'
readonly GREEN=$'\033[0;32m'
readonly YELLOW=$'\033[1;33m'
readonly BLUE=$'\033[0;34m'
readonly BOLD=$'\033[1m'
readonly NC=$'\033[0m' # No Color

# --- Configuration & Path Resolution ---
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
USER_SCRIPTS_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)"

# Resolve user and home context (handles direct user, sudo, orchestrator, and installer execution)
if (( EUID == 0 )); then
    TARGET_USER=""
    for candidate in "${TARGET_USER:-}" "${DUSKY_TARGET_USER:-}" "${DUSKY_USER:-}" "${SUDO_USER:-}" "${DOAS_USER:-}"; do
        if [[ -n "$candidate" && "$candidate" != "root" ]] && id "$candidate" &>/dev/null; then
            TARGET_USER="$candidate"
            break
        fi
    done
    if [[ -z "$TARGET_USER" ]]; then
        # Find first non-root interactive user (UID >= 1000)
        TARGET_USER="$(awk -F: '$3 >= 1000 && $3 < 60000 && $7 !~ /nologin|false/ {print $1; exit}' /etc/passwd 2>/dev/null || true)"
        [[ -n "$TARGET_USER" ]] || TARGET_USER="root"
    fi
    TARGET_HOME="${DUSKY_USER_HOME:-$(getent passwd "$TARGET_USER" 2>/dev/null | cut -d: -f6)}"
    [[ -n "$TARGET_HOME" ]] || TARGET_HOME="${HOME}"
else
    TARGET_USER="$(id -un)"
    TARGET_HOME="${HOME}"
fi

readonly SERVICE_NAME="dusky_battery.service"
readonly CONFIG_DIR="${XDG_CONFIG_HOME:-"${TARGET_HOME}/.config"}"
readonly SYSTEMD_USER_DIR="${CONFIG_DIR}/systemd/user"

if [[ -f "${USER_SCRIPTS_DIR}/battery/notify/${SERVICE_NAME}" ]]; then
    readonly SOURCE_FILE="${USER_SCRIPTS_DIR}/battery/notify/${SERVICE_NAME}"
    readonly SCRIPT_FILE="${USER_SCRIPTS_DIR}/battery/notify/battery_notify.sh"
else
    readonly SOURCE_FILE="${TARGET_HOME}/user_scripts/battery/notify/${SERVICE_NAME}"
    readonly SCRIPT_FILE="${TARGET_HOME}/user_scripts/battery/notify/battery_notify.sh"
fi
readonly TARGET_FILE="${SYSTEMD_USER_DIR}/${SERVICE_NAME}"

# --- Helper Functions ---
log_info() {
    printf '%s[INFO]%s %s\n' "${BLUE}" "${NC}" "$1"
}

log_success() {
    printf '%s[OK]%s %s\n' "${GREEN}" "${NC}" "$1"
}

log_warn() {
    printf '%s[WARN]%s %s\n' "${YELLOW}" "${NC}" "$1"
}

log_error() {
    printf '%s[ERROR]%s %s\n' "${RED}" "${NC}" "$1" >&2
}

run_user_systemctl() {
    if (( EUID == 0 )) && [[ -n "${TARGET_USER:-}" && "$TARGET_USER" != "root" ]]; then
        local user_uid
        user_uid=$(id -u "$TARGET_USER" 2>/dev/null || true)
        if [[ -n "$user_uid" && -d "/run/user/$user_uid" ]]; then
            runuser -u "$TARGET_USER" -- env XDG_RUNTIME_DIR="/run/user/$user_uid" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$user_uid/bus" systemctl --user "$@"
        else
            return 1
        fi
    else
        systemctl --user "$@"
    fi
}

TEMP_UNIT=""

# Cleanup/Error Trap
cleanup() {
    local exit_code=$?
    [[ -z "$TEMP_UNIT" ]] || rm -f -- "$TEMP_UNIT"
    # Suppress message for user-initiated exits (Ctrl+C = 130, etc.)
    if [[ ${exit_code} -ne 0 && ${exit_code} -lt 128 ]]; then
        log_error "Script failed with exit code ${exit_code}."
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

# --- State Detection Functions ---

has_battery() {
    local info type_file type_val scope present
    # DisplayDevice excludes peripheral batteries and includes system UPS devices.
    if command -v upower >/dev/null &&
        info=$(LC_ALL=C upower --show-info /org/freedesktop/UPower/devices/DisplayDevice 2>/dev/null); then
        if [[ $info =~ present:[[:space:]]*yes([[:space:]]|$) &&
              $info =~ power\ supply:[[:space:]]*yes([[:space:]]|$) &&
              $info =~ $'\n'[[:space:]]*(battery|ups)([[:space:]]|$) ]]; then
            return 0
        fi
    fi
    # Allow installation while the daemon is unavailable, using kernel metadata.
    for type_file in /sys/class/power_supply/*/type; do
        [[ -r $type_file ]] || continue
        IFS= read -r type_val < "$type_file" || continue
        [[ $type_val == Battery || $type_val == UPS ]] || continue
        scope=System present=1
        if [[ -r ${type_file%type}scope ]]; then
            IFS= read -r scope < "${type_file%type}scope" || continue
        fi
        if [[ -r ${type_file%type}present ]]; then
            IFS= read -r present < "${type_file%type}present" || continue
        fi
        [[ $scope == System && $present == 1 ]] && return 0
    done
    return 1
}

is_service_installed() {
    [[ -f "${TARGET_FILE}" || -L "${TARGET_FILE}" ]]
}

is_service_loaded() {
    [[ $(run_user_systemctl show --property=LoadState --value "${SERVICE_NAME}" 2>/dev/null) == loaded ]]
}

is_service_enabled() {
    run_user_systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null || [[ -e "${SYSTEMD_USER_DIR}/hyprland-session.target.wants/${SERVICE_NAME}" || -L "${SYSTEMD_USER_DIR}/hyprland-session.target.wants/${SERVICE_NAME}" ]]
}

is_service_active() {
    run_user_systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null
}

# --- Action Functions ---

do_install() {
    log_info "Initializing battery notify installation..."

    # 1. Validation: Ensure source exists
    if [[ ! -f "${SOURCE_FILE}" ]]; then
        log_error "Source file not found at: ${SOURCE_FILE}"
        return 1
    fi

    if [[ ! -f "${SCRIPT_FILE}" ]]; then
        log_error "Monitor script not found at: ${SCRIPT_FILE}"
        return 1
    fi
    local cmd
    for cmd in upower busctl systemctl; do
        if ! command -v "$cmd" >/dev/null; then
            log_error "Required command not found: $cmd"
            return 1
        fi
    done

    # 2. Preparation: Ensure target directory exists
    if [[ ! -d "${SYSTEMD_USER_DIR}" ]]; then
        log_info "Creating systemd user directory: ${SYSTEMD_USER_DIR}"
        mkdir -p "${SYSTEMD_USER_DIR}"
        if (( EUID == 0 )) && [[ -n "${TARGET_USER:-}" && "$TARGET_USER" != "root" ]]; then
            chown -R "$TARGET_USER:$TARGET_USER" "${CONFIG_DIR}/systemd" 2>/dev/null || true
        fi
    fi

    # 3. Replace atomically, including a stale symlink, after a successful copy.
    log_info "Installing service file..."
    chmod +x "${SCRIPT_FILE}"
    TEMP_UNIT=$(mktemp "${SYSTEMD_USER_DIR}/.${SERVICE_NAME}.XXXXXX")
    install --mode=644 --no-target-directory -- "${SOURCE_FILE}" "$TEMP_UNIT"
    mv --force --no-target-directory -- "$TEMP_UNIT" "${TARGET_FILE}"
    TEMP_UNIT=""

    if (( EUID == 0 )) && [[ -n "${TARGET_USER:-}" && "$TARGET_USER" != "root" ]]; then
        chown "$TARGET_USER:$TARGET_USER" "${TARGET_FILE}" 2>/dev/null || true
    fi

    # 4. Systemd registration
    log_info "Reloading systemd user daemon..."
    run_user_systemctl daemon-reload 2>/dev/null || true

    # 5. Enable service (with direct wants symlink fallback for offline/chroot environments)
    log_info "Enabling ${SERVICE_NAME}..."
    if ! run_user_systemctl enable "${SERVICE_NAME}" 2>/dev/null; then
        local wants_dir="${SYSTEMD_USER_DIR}/hyprland-session.target.wants"
        mkdir -p "$wants_dir"
        ln -sf "${TARGET_FILE}" "${wants_dir}/${SERVICE_NAME}"
        if (( EUID == 0 )) && [[ -n "${TARGET_USER:-}" && "$TARGET_USER" != "root" ]]; then
            chown -h "$TARGET_USER:$TARGET_USER" "${wants_dir}/${SERVICE_NAME}" 2>/dev/null || true
            chown "$TARGET_USER:$TARGET_USER" "$wants_dir" 2>/dev/null || true
        fi
    fi

    # 6. Restart/Start if running in an active user session
    if run_user_systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
        log_info "Restarting active service ${SERVICE_NAME}..."
        run_user_systemctl restart "${SERVICE_NAME}" 2>/dev/null || true
    elif run_user_systemctl is-active --quiet hyprland-session.target 2>/dev/null; then
        log_info "Starting service ${SERVICE_NAME} in active session..."
        run_user_systemctl start "${SERVICE_NAME}" 2>/dev/null || true
    fi

    log_success "Battery notification service installed and enabled."
}

do_uninstall() {
    log_info "Initializing battery notify removal..."

    local changed=false

    # 1. Stop loaded units even while they are activating or waiting to restart.
    if is_service_loaded || is_service_active; then
        log_info "Stopping ${SERVICE_NAME}..."
        run_user_systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
        changed=true
    fi

    # 2. Disable the service if it's currently enabled
    if is_service_enabled; then
        log_info "Disabling ${SERVICE_NAME}..."
        run_user_systemctl disable "${SERVICE_NAME}" 2>/dev/null || true
        changed=true
    fi

    # 3. Clean up wants symlinks (ensures no dangling symlinks)
    local wants_link="${SYSTEMD_USER_DIR}/hyprland-session.target.wants/${SERVICE_NAME}"
    if [[ -L "$wants_link" || -f "$wants_link" ]]; then
        log_info "Removing symlink: ${wants_link}"
        rm -f "$wants_link"
        changed=true
    fi

    # 4. Remove the installed service file
    if is_service_installed; then
        log_info "Removing service file: ${TARGET_FILE}"
        rm -f "${TARGET_FILE}"
        changed=true
    fi

    # 5. Reload daemon so systemd forgets the unit
    if [[ "${changed}" == true ]]; then
        log_info "Reloading systemd user daemon..."
        run_user_systemctl daemon-reload 2>/dev/null || true
        run_user_systemctl reset-failed "${SERVICE_NAME}" 2>/dev/null || true
    fi

    log_success "Battery notification service has been fully removed."
}

# --- UI Function (Interactive Mode) ---

show_interactive_ui() {
    local battery_status battery_present=false
    if has_battery; then
        battery_present=true
        battery_status="${GREEN}Detected${NC}"
    else
        battery_status="${YELLOW}Not detected${NC}"
    fi

    local service_status
    if is_service_installed; then
        if is_service_active; then
            service_status="${GREEN}Installed and running${NC}"
        elif is_service_enabled; then
            service_status="${YELLOW}Installed and enabled (not currently active)${NC}"
        else
            service_status="${YELLOW}Installed but not enabled${NC}"
        fi
    else
        service_status="${RED}Not installed${NC}"
    fi

    printf '\n'
    printf '%s══════════════════════════════════════════════%s\n' "${BOLD}" "${NC}"
    printf '%s   Battery Notification Service Manager%s\n' "${BOLD}" "${NC}"
    printf '%s══════════════════════════════════════════════%s\n' "${BOLD}" "${NC}"
    printf '  Battery:  %b\n' "${battery_status}"
    printf '  Service:  %b\n' "${service_status}"
    printf '%s══════════════════════════════════════════════%s\n' "${BOLD}" "${NC}"
    printf '\n'

    # If no battery, warn the user but still let them choose
    if [[ $battery_present == false ]]; then
        log_warn "No battery detected. This service is intended for laptops with batteries."
        log_warn "Installing on a desktop or battery-less system is not recommended."
        printf '\n'
    fi

    printf '  %s1)%s Install / Re-install and enable the service\n' "${BOLD}" "${NC}"
    printf '  %s2)%s Uninstall and disable the service (undo)\n' "${BOLD}" "${NC}"
    printf '  %s3)%s Exit without changes\n' "${BOLD}" "${NC}"
    printf '\n'

    local choice
    while true; do
        if ! read -rp "  Select an option [1-3]: " choice; then
            # EOF (Ctrl+D) — treat as exit
            printf '\n'
            log_info "Exiting without changes."
            return 0
        fi
        case "${choice}" in
            1)
                printf '\n'
                if [[ $battery_present == false ]]; then
                    local confirm=""
                    if ! read -rp "${BLUE}[QUERY]${NC} No battery present. Proceed anyway? (y/N): " confirm; then
                        # EOF (Ctrl+D) — treat as "No"
                        printf '\n'
                        log_info "Installation cancelled by user."
                        return 0
                    fi
                    if [[ ! "${confirm}" =~ ^[Yy]$ ]]; then
                        log_info "Installation cancelled by user."
                        return 0
                    fi
                fi
                do_install
                return 0
                ;;
            2)
                printf '\n'
                if ! is_service_installed && ! is_service_enabled && ! is_service_loaded; then
                    log_info "Service is not installed. Nothing to uninstall."
                    return 0
                fi
                do_uninstall
                return 0
                ;;
            3)
                log_info "Exiting without changes."
                return 0
                ;;
            *)
                log_error "Invalid selection. Please enter 1, 2, or 3."
                ;;
        esac
    done
}

# --- Main Logic ---

main() {
    case "${1:-}" in
        --auto)
            if ! has_battery; then
                log_info "Auto-mode: No system battery or UPS detected. Skipping installation."
                return 0
            fi
            do_install
            return
            ;;
        -u|--uninstall|--remove)
            do_uninstall
            return
            ;;
        -h|--help)
            printf 'Usage: %s [--auto | --uninstall | --help]\n' "${0##*/}"
            printf 'Without options, manage the battery service interactively.\n'
            return 0
            ;;
        "") ;;
        *)
            log_error "Unknown option: $1"
            return 2
            ;;
    esac

    # --- Interactive Mode: Always show UI ---
    show_interactive_ui
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi
