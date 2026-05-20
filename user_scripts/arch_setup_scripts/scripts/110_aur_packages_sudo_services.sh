#!/usr/bin/env bash
# Enables Root services for Aur packages
# ==============================================================================
# ARCH LINUX SYSTEM SERVICE ENABLER (Arch/Hyprland/UWSM Context)
# ==============================================================================
# Description: Enables systemd services safely and sequentially.
#              Skips missing services without breaking execution.
# Logic:       Checks EUID -> Elevates if needed -> Iterates Array -> Checks Unit Existence -> Enables
# Standards:   Bash 5+, set -euo pipefail, No Logs, Auto-Sudo
# ==============================================================================

# --- 1. Strict Error Handling & Safety ---
set -euo pipefail

# --- 2. Configuration (User Editable) ---
# Add your system services here.
readonly TARGET_SERVICES=(
  "fwupd.service"
  "warp-svc.service"
  "preload"
  "asusd.service"
  "input-remapper.service"
  # Add more services below:
  # "bluetooth.service"
)

# --- 3. Formatting & Visuals ---
# We use $'\e...' to ensure the escape character is interpreted correctly
readonly C_RESET=$'\e[0m'
readonly C_BOLD=$'\e[1m'
readonly C_GREEN=$'\e[32m'
readonly C_YELLOW=$'\e[33m'
readonly C_RED=$'\e[31m'
readonly C_BLUE=$'\e[34m'

log_info() { printf "${C_BLUE}[INFO]${C_RESET}  %s\n" "$1"; }
log_success() { printf "${C_GREEN}[OK]${C_RESET}    %s\n" "$1"; }
log_warn() { printf "${C_YELLOW}[SKIP]${C_RESET}  %s\n" "$1"; }
log_err() { printf "${C_RED}[FAIL]${C_RESET}  %s\n" "$1"; }

# --- 4. Cleanup Trap ---
cleanup() {
  # Reset terminal colors on exit/interruption
  printf "%s" "${C_RESET}"
}
trap cleanup EXIT INT TERM

# --- 5. Privilege Escalation (Auto-Sudo) ---
# If not running as root, re-execute script with sudo preserving environment
if [[ $EUID -ne 0 ]]; then
  log_info "Root privileges required. Elevating..."
  exec sudo -E "$0" "$@"
fi

# --- 6. Main Logic ---
main() {
  printf "\n${C_BOLD}Starting System Service Initialization...${C_RESET}\n"
  printf "${C_BOLD}-----------------------------------------${C_RESET}\n"

  local svc_name

  for svc_name in "${TARGET_SERVICES[@]}"; do
    # 6a. Check if the unit actually exists
    if systemctl list-unit-files "$svc_name" &>/dev/null; then

      # 6b. Attempt to enable and start
      # capturing stderr to keep output clean
      if output=$(systemctl enable --now "$svc_name" 2>&1); then
        log_success "Enabled & Started: ${C_BOLD}$svc_name${C_RESET}"
      else
        log_err "Could not enable $svc_name. Reason:"
        printf "      %s\n" "$output"
      fi

    else
      # 6c. Handle missing services gracefully
      log_warn "Service not found: ${C_BOLD}$svc_name${C_RESET}. Skipping..."
    fi
  done

  printf "${C_BOLD}-----------------------------------------${C_RESET}\n"
  # The \n inside the string is handled by printf automatically
  log_info "Operation complete."
  printf "\n"
}

main "$@"
