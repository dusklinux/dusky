#!/usr/bin/env bash
# Installs/uninstalls the battery_notify service.
# -----------------------------------------------------------------------------
# Script: install_battery_notify.sh
# Description: Installs (copies) and enables the battery_notify service,
#              or uninstalls (removes) and disables it.
#              When run with --auto, operates non-interactively.
# Environment: Arch Linux / Hyprland (Wayland) / UWSM
# Author: DevOps Assistant
# -----------------------------------------------------------------------------

# --- Strict Error Handling ---
set -euo pipefail

# --- Styling & Colors ---
readonly RED=$'\033[0;31m'
readonly GREEN=$'\033[0;32m'
readonly YELLOW=$'\033[1;33m'
readonly BLUE=$'\033[0;34m'
readonly BOLD=$'\033[1m'
readonly NC=$'\033[0m' # No Color

# --- Configuration ---
readonly SERVICE_NAME="dusky_battery.service"
readonly CONFIG_DIR="${XDG_CONFIG_HOME:-"${HOME}/.config"}"
readonly SYSTEMD_USER_DIR="${CONFIG_DIR}/systemd/user"
readonly SOURCE_FILE="${HOME}/user_scripts/battery/notify/${SERVICE_NAME}"
readonly TARGET_FILE="${SYSTEMD_USER_DIR}/${SERVICE_NAME}"
readonly SCRIPT_FILE="${HOME}/user_scripts/battery/notify/battery_notify.sh"

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

# Cleanup/Error Trap
cleanup() {
    local exit_code=$?
    # Suppress message for user-initiated exits (Ctrl+C = 130, etc.)
    if [[ ${exit_code} -ne 0 && ${exit_code} -lt 128 ]]; then
        log_error "Script failed with exit code ${exit_code}."
    fi
}
trap cleanup EXIT

# --- State Detection Functions ---

has_battery() {
    # 1. Try upower if available (preferred)
    if command -v upower &>/dev/null; then
        if upower -e 2>/dev/null | grep -qiE 'BAT|battery'; then
            return 0
        fi
    fi

    # 2. Fallback to sysfs check of type
    local type_file
    for type_file in /sys/class/power_supply/*/type; do
        if [[ -f "${type_file}" ]]; then
            local type_val
            type_val=$(tr '[:upper:]' '[:lower:]' < "${type_file}" 2>/dev/null | tr -d '[:space:]') || true
            if [[ "${type_val}" == "battery" ]]; then
                return 0
            fi
        fi
    done

    # 3. Fallback to simple glob
    if compgen -G "/sys/class/power_supply/BAT*" > /dev/null 2>&1; then
        return 0
    fi

    return 1
}

is_service_installed() {
    [[ -f "${TARGET_FILE}" ]]
}

is_service_enabled() {
    systemctl --user is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null
}

is_service_active() {
    systemctl --user is-active --quiet "${SERVICE_NAME}" 2>/dev/null
}

# --- Action Functions ---

do_install() {
    log_info "Initializing battery notify installation..."

    # 1. Validation: Ensure source exists
    if [[ ! -f "${SOURCE_FILE}" ]]; then
        log_error "Source file not found at: ${SOURCE_FILE}"
        return 1
    fi

    # 2. Preparation: Ensure target directory exists
    if [[ ! -d "${SYSTEMD_USER_DIR}" ]]; then
        log_info "Creating systemd user directory: ${SYSTEMD_USER_DIR}"
        mkdir -p "${SYSTEMD_USER_DIR}"
    fi

    # 3. Copy the service file
    # Remove target first in case it's a stale symlink from an old install
    log_info "Installing service file (copying)..."
    rm -f "${TARGET_FILE}"
    cp -f "${SOURCE_FILE}" "${TARGET_FILE}"
    chmod +x "${SCRIPT_FILE}"

    # 4. Systemd registration
    log_info "Reloading systemd user daemon..."
    systemctl --user daemon-reload

    # 5. Enable and (re)start — restart covers both fresh start and re-install
    log_info "Enabling and (re)starting ${SERVICE_NAME}..."
    systemctl --user enable "${SERVICE_NAME}"
    systemctl --user restart "${SERVICE_NAME}"

    log_success "Battery notification service installed and running."
}

do_uninstall() {
    log_info "Initializing battery notify removal..."

    local changed=false

    # 1. Stop the service if it's currently active
    if is_service_active; then
        log_info "Stopping ${SERVICE_NAME}..."
        systemctl --user stop "${SERVICE_NAME}"
        changed=true
    fi

    # 2. Disable the service if it's currently enabled
    if is_service_enabled; then
        log_info "Disabling ${SERVICE_NAME}..."
        systemctl --user disable "${SERVICE_NAME}"
        changed=true
    fi

    # 3. Remove the installed service file
    if is_service_installed; then
        log_info "Removing service file: ${TARGET_FILE}"
        rm -f "${TARGET_FILE}"
        changed=true
    fi

    # 4. Reload daemon so systemd forgets the unit
    if [[ "${changed}" == true ]]; then
        log_info "Reloading systemd user daemon..."
        systemctl --user daemon-reload
        # Reset any failed state that might linger
        systemctl --user reset-failed "${SERVICE_NAME}" 2>/dev/null || true
    fi

    log_success "Battery notification service has been fully removed."
}

# --- UI Function (Interactive Mode) ---

show_interactive_ui() {
    local battery_status
    if has_battery; then
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
    if ! has_battery; then
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
                if ! has_battery; then
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
                if ! is_service_installed && ! is_service_enabled && ! is_service_active; then
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
    # --- Argument Parsing ---
    local auto_mode=false
    for arg in "$@"; do
        if [[ "${arg}" == "--auto" ]]; then
            auto_mode=true
            break
        fi
    done

    # --- Auto Mode: Original non-interactive behavior, completely unchanged ---
    if [[ "${auto_mode}" == true ]]; then
        if ! has_battery; then
            log_info "Auto-mode: No battery detected. Skipping installation."
            exit 0
        fi
        do_install
        exit 0
    fi

    # --- Interactive Mode: Always show UI ---
    show_interactive_ui
}

main "$@"
