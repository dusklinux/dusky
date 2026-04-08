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
    "tlp.service"
    "udisks2.service"
    "thermald.service"
    "bluetooth.service"
    "firewalld.service"
    "fstrim.timer"
    "systemd-timesyncd.service"
    "acpid.service"
    "vsftpd.service"
    "reflector.timer"
    "swayosd-libinput-backend.service"
    "systemd-resolved.service"
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

# 4. Helper: Check if Unit Exists
unit_exists() {
    systemctl cat "$1" &>/dev/null
}

# 5. Main Execution
main() {
    log_info "Initializing Service Activation..."

    if ! command -v systemctl &>/dev/null; then
        log_err "systemctl not found. Ensure you are inside arch-chroot."
        exit 1
    fi

    local service
    local output
    # Array to track which services failed
    local -a failed_services=()

    for service in "${SERVICES[@]}"; do
        # 1. Validation: Does the unit file exist?
        if ! unit_exists "$service"; then
            log_err "Skipping $service: Unit not found (Package not installed?)"
            failed_services+=("$service (Missing)")
            continue # Skip to next iteration
        fi

        # 2. Enablement
        # 'if' suppresses 'set -e' for the command inside it
        if output=$(systemctl enable "$service" --force 2>&1); then
            log_success "Enabled: $service"
        else
            log_err "Failed to enable $service"
            printf "%s\n" "$output" >&2
            failed_services+=("$service (Systemd Error)")
            # We do NOT exit here; we just continue
        fi
    done

    # 6. Final Summary
    echo "" # Newline for readability
    if [ ${#failed_services[@]} -eq 0 ]; then
        log_success "All services enabled successfully."
        exit 0
    else
        log_warn "Service activation completed with errors."
        log_warn "The following services could not be enabled:"
        for fail in "${failed_services[@]}"; do
            printf "  - %s\n" "$fail"
        done
        
        # We exit with 1 so the Master script knows it wasn't a perfect run.
        # If you want the master script to ignore this, handle the exit code there.
        exit 1
    fi
}

main
