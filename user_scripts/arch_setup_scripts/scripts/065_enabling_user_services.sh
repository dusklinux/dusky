#!/usr/bin/env bash
# Enables user services
# ==============================================================================
# Script Name: enable_hyprland_services_v2.sh
# Description: Fixed logic for enabling Arch/Hyprland user services.
# ==============================================================================

# --- Safety & Strict Mode ---
set -euo pipefail

# --- Presentation & Colors (Fixed for compatibility) ---
# We use ANSI-C quoting $'...' to ensure the escape codes are interpreted correctly.
readonly C_RESET=$'\e[0m'
readonly C_GREEN=$'\e[1;32m'
readonly C_RED=$'\e[1;31m'
readonly C_BLUE=$'\e[1;34m'
readonly C_YELLOW=$'\e[1;33m'
readonly C_BOLD=$'\e[1m'

# --- Logging Helper ---
log() {
    local level="$1"
    local message="$2"
    case "$level" in
        INFO)    printf "${C_BLUE}[INFO]${C_RESET}  %s\n" "$message" ;;
        SUCCESS) printf "${C_GREEN}[OK]${C_RESET}    %s\n" "$message" ;;
        WARN)    printf "${C_YELLOW}[WARN]${C_RESET}  %s\n" "$message" ;;
        ERROR)   printf "${C_RED}[FAIL]${C_RESET}  %s\n" "$message" ;;
    esac
}

# --- 1. Root Privilege Check ---
if [[ $EUID -eq 0 ]]; then
    log ERROR "Do NOT run user service scripts as root."
    exit 1
fi

# --- 2. Service Definition ---
services=(
    "pipewire.socket"
    "pipewire-pulse.socket"
    "wireplumber.service"
    "hypridle.service"
    "hyprpolkitagent.service"
    "fumon.service"
    "gnome-keyring-daemon.service"
    "gnome-keyring-daemon.socket"
    "mako.service"
#    "hyprsunset.service"
)

# --- 3. Main Logic ---
main() {
    log INFO "Initializing UWSM/Hyprland User Service Setup..."
    
    # Initialize integers explicitly
    local success_count=0
    local fail_count=0

    for unit in "${services[@]}"; do
        # 1. Check if unit exists (avoid systemctl noise)
        if ! systemctl --user list-unit-files "$unit" &>/dev/null; then
             log WARN "Unit ${C_BOLD}$unit${C_RESET} not found. Skipped."
             fail_count=$((fail_count + 1))
             continue
        fi

        # 2. Attempt enable --now
        # We use a wrapper "if" to prevent set -e from killing the script on failure
        if output=$(systemctl --user enable --now "$unit" 2>&1); then
            log SUCCESS "Enabled: ${C_BOLD}$unit${C_RESET}"
            # SAFE ARITHMETIC: No (( ++ )) here to avoid set -e trap on zero
            success_count=$((success_count + 1))
        else
            log ERROR "Failed: ${C_BOLD}$unit${C_RESET}"
            printf "      └─ %s\n" "$output"
            fail_count=$((fail_count + 1))
        fi
    done

    # --- 4. Summary ---
    printf "\n"
    log INFO "Done. Success: ${success_count} | Skipped/Failed: ${fail_count}"
    
    # Reload the user daemon to ensure UWSM picks up changes immediately
    systemctl --user daemon-reload
}

main
