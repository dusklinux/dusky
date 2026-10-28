#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Script: 05-enable-services.sh
# Description: Enables systemd services with fault tolerance.
# -----------------------------------------------------------------------------

# 1. Strict Mode
set -u
# Note: We removed 'set -e' temporarily or handle it carefully. 
# Since we handle errors manually in the loop, 'set -e' is fine 
# as long as we use if-statements for commands that might fail.
set -o pipefail
IFS=$'\n\t'

# 2. Configuration
readonly SERVICES=(
    "NetworkManager.service"
    "sshd.service"
    "udisks2.service"
    "thermald.service"
    "bluetooth.service"
    "ufw.service"
    "fstrim.timer"
    "systemd-timesyncd.service"
    "acpid.service"
    "systemd-resolved.service"
    "snapper-cleanup.timer"
)

# 3. Formatting
readonly C_RESET=$'\033[0m'
readonly C_GREEN=$'\033[1;32m'
readonly C_BLUE=$'\033[1;34m'
readonly C_RED=$'\033[1;31m'
readonly C_YELLOW=$'\033[1;33m'

log_info()    { printf "${C_BLUE}[INFO]${C_RESET} %s\n" "$*"; }
log_success() { printf "${C_GREEN}[OK]${C_RESET} %s\n" "$*"; }
log_err()     { printf "${C_RED}[ERROR]${C_RESET} %s\n" "$*" >&2; }
log_warn()    { printf "${C_YELLOW}[WARN]${C_RESET} %s\n" "$*" >&2; }

# Helper: Configure SSH root login
configure_ssh() {
    log_info "Configuring SSH root login..."
    install -d -m0755 /etc/ssh/sshd_config.d || return 1
    printf '%s\n' "PermitRootLogin yes" > /etc/ssh/sshd_config.d/permit_root.conf || return 1
    chmod 644 /etc/ssh/sshd_config.d/permit_root.conf || return 1
    log_success "Configured /etc/ssh/sshd_config.d/permit_root.conf"
}

# Restrict sysfs writes to whole disks and schedulers exposed by this kernel.
configure_io_schedulers() {
    install -d -m0755 /etc/udev/rules.d || return 1
    local temporary
    temporary=$(mktemp /etc/udev/rules.d/.dusky-ioscheduler.XXXXXXXX) || return 1
    if ! cat -- "${BASH_SOURCE[0]%/*}/assets/60-dusky-ioscheduler.rules" > "$temporary"
    then
        rm -f -- "$temporary"
        return 1
    fi
    chmod 644 "$temporary" && mv -f -- "$temporary" /etc/udev/rules.d/60-dusky-ioscheduler.rules
}

# 4. Helper: Check if Unit Exists
unit_exists() {
    local units
    units=$(systemctl --root=/ list-unit-files --no-legend --no-pager "$1") || return 1
    [[ -n $units ]]
}

# 5. Main Execution
main() {
    log_info "Initializing Service Activation..."

    if ! command -v systemctl &>/dev/null; then
        log_err "systemctl not found. Ensure you are inside arch-chroot."
        exit 1
    fi

    configure_ssh || exit 1
    configure_io_schedulers || exit 1

    local service
    local enable_failures=0
    local output
    local -a failed_services=()

    for service in "${SERVICES[@]}"; do
        if ! unit_exists "$service"; then
            log_warn "Skipping $service: Unit not found (Package not installed?)"
            failed_services+=("$service (Missing)")
            continue
        fi

        if output=$(systemctl --root=/ --no-reload enable "$service" --force 2>&1); then
            log_success "Enabled: $service"
        else
            log_err "Failed to enable $service"
            printf "%s\n" "$output" >&2
            failed_services+=("$service (Systemd Error)")
            enable_failures=1
        fi
    done

    echo ""
    if [ ${#failed_services[@]} -eq 0 ]; then
        log_success "All services enabled successfully."
    else
        log_warn "Service activation completed with optional missing units."
        for fail in "${failed_services[@]}"; do
            printf "  - %s\n" "$fail"
        done
    fi
    return "$enable_failures"
}

main
