#!/usr/bin/env bash
# Enables systemd system services for packages
# ==============================================================================
# Arch Linux System Service Initializer
# Context: Hyprland / UWSM / Systemd
# ==============================================================================

# ------------------------------------------------------------------------------
# 1. Strict Environment & Error Handling
# ------------------------------------------------------------------------------
set -euo pipefail
IFS=$'\n\t'

# Trap for clean exit (no temp files to clean, but good practice)
trap 'exit_code=$?; [[ $exit_code -ne 0 ]] && printf "\n[!] Script failed with code %d\n" "$exit_code"' EXIT

# ------------------------------------------------------------------------------
# 2. Configuration (User Editable)
# ------------------------------------------------------------------------------
# Add or remove system services here.
readonly TARGET_SERVICES=(
# --- NVIDIA Power Management (Safe to include; skipped if missing) ---
    "nvidia-suspend.service"
    "nvidia-hibernate.service"
    "nvidia-resume.service"

    # Optional: Dynamic Boost for modern Turing+ Laptops
    "nvidia-powerd.service"
)

# ------------------------------------------------------------------------------
# 3. Privilege Escalation (Auto-Sudo)
# ------------------------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
   printf "[\033[0;33mINFO\033[0m] Escalating permissions to root...\n"
   exec sudo "$0" "$@"
fi

# ------------------------------------------------------------------------------
# 4. Helpers (Logging & Logic)
# ------------------------------------------------------------------------------
log_info()    { printf "[\033[0;34mINFO\033[0m] %s\n" "$1"; }
log_success() { printf "[\033[0;32m OK \033[0m] %s\n" "$1"; }
log_warn()    { printf "[\033[0;33mWARN\033[0m] %s\n" "$1"; }
log_err()     { printf "[\033[0;31mERR \033[0m] %s\n" "$1"; }

enable_service() {
    local service="$1"

    # Check if the unit file exists effectively without forking grep
    if systemctl list-unit-files "$service" &>/dev/null; then
        # Check if already enabled to avoid redundant systemd output
        if systemctl is-enabled --quiet "$service"; then
            log_info "$service is already enabled."
        else
            # Try to enable and start immediately
            if systemctl enable --now "$service" &>/dev/null; then
                log_success "Enabled & Started: $service"
            else
                log_err "Failed to enable: $service (Check logs)"
            fi
        fi
    else
        log_warn "Skipping: $service (Package not installed / Unit not found)"
    fi
}

# ------------------------------------------------------------------------------
# 5. Main Execution
# ------------------------------------------------------------------------------
main() {
    printf "\n--- Arch System Service Optimization ---\n"
    
    for service in "${TARGET_SERVICES[@]}"; do
        enable_service "$service"
    done

    # UWSM/Hyprland Note: 
    # System services handle hardware/network. 
    # User-session services should be handled by 'uwsm app' or systemd --user.
    
    printf "\n--- Operation Complete ---\n"
}

main
