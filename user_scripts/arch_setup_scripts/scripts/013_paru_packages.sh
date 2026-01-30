#!/usr/bin/env bash
# To install AUR packages
# ==============================================================================
# Script Name: install_pkg_manifest.sh
# Description: Autonomous AUR/Repo package installer with hybrid batch/loop logic.
# Context:     Arch Linux (Rolling) | Hyprland | UWSM
# ==============================================================================

# ------------------------------------------------------------------------------
# 1. STRICT SAFETY & SETTINGS
# ------------------------------------------------------------------------------
# -u: Treat unset variables as an error
# -o pipefail: Pipeline fails if any command fails
set -uo pipefail

# ------------------------------------------------------------------------------
# 2. VISUALS & LOGGING
# ------------------------------------------------------------------------------
readonly C_RESET=$'\033[0m'
readonly C_BOLD=$'\033[1m'
readonly C_GREEN=$'\033[1;32m'
readonly C_BLUE=$'\033[1;34m'
readonly C_YELLOW=$'\033[1;33m'
readonly C_RED=$'\033[1;31m'
readonly C_CYAN=$'\033[1;36m'

# Improved logging: Uses "$*" to accept multiple arguments and handles formatting safely
log_info()    { printf "%s[INFO]%s %s\n" "${C_BLUE}" "${C_RESET}" "$*"; }
log_success() { printf "%s[SUCCESS]%s %s\n" "${C_GREEN}" "${C_RESET}" "$*"; }
log_warn()    { printf "%s[WARN]%s %s\n" "${C_YELLOW}" "${C_RESET}" "$*" >&2; }
log_err()     { printf "%s[ERROR]%s %s\n" "${C_RED}" "${C_RESET}" "$*" >&2; }
log_task()    { printf "\n%s%s:: %s%s\n" "${C_BOLD}" "${C_CYAN}" "$*" "${C_RESET}"; }

# ------------------------------------------------------------------------------
# 3. CLEANUP & TRAPS
# ------------------------------------------------------------------------------
cleanup() {
  # Reset colors on exit
  printf "%s" "${C_RESET}"
}
# Catch EXIT as well as Interrupts (Ctrl+C) and Termination signals
trap cleanup EXIT INT TERM

# ------------------------------------------------------------------------------
# 4. CONFIGURATION
# ------------------------------------------------------------------------------
#
# installing manually with script because this is a massive package that includes every cursor theme
# "bibata-cursor-theme-bin"

readonly PACKAGES=(
  "wlogout"
  "adwaita-qt6"
  "adwaita-qt5"
  "otf-atkinson-hyperlegible-next"
  "fluent-icon-theme-git"
  "python-pywalfox"
  "hyprshade"
  "waypaper"
  "peaclock"
  "tray-tui"
  "wifitui-bin"
  "xdg-terminal-exec"
  "vesktop"
)

# 5 Seconds wait time before auto-retrying
readonly TIMEOUT_SEC=5
# 6 Retries per package/operation
readonly MAX_RETRIES=6

# ------------------------------------------------------------------------------
# 5. PRE-FLIGHT CHECKS
# ------------------------------------------------------------------------------
if [[ $EUID -eq 0 ]]; then
  log_err "This script must NOT be run as root."
  log_err "AUR helpers handle privileges internally."
  exit 1
fi

if command -v paru &>/dev/null; then
  readonly AUR_HELPER="paru"
elif command -v yay &>/dev/null; then
  readonly AUR_HELPER="yay"
else
  log_err "AUR helper (paru/yay) not found. Please install one first."
  exit 1
fi

# ------------------------------------------------------------------------------
# 6. MAIN LOGIC
# ------------------------------------------------------------------------------
main() {
  log_task "Starting Autonomous Package Installation Sequence"
  log_info "Using AUR Helper: ${AUR_HELPER}"
  log_info "Retry Policy: ${MAX_RETRIES} attempts | ${TIMEOUT_SEC}s delay"

  # --------------------------------------------------------------------------
  # STEP 1: Full System Update (Now with Retry Logic)
  # --------------------------------------------------------------------------
  log_task "Synchronizing Repositories & Updating System..."
  
  local update_success=false
  # Retry loop for system update to handle mirror timeouts
  for ((i=1; i<=MAX_RETRIES; i++)); do
    # -Syu is critical to prevent partial upgrades
    if "$AUR_HELPER" -Syu --noconfirm; then
      update_success=true
      break
    else
      log_warn "System update failed (Attempt $i/$MAX_RETRIES). Retrying in 5 seconds..."
      sleep 5
    fi
  done

  if [[ "$update_success" == "false" ]]; then
    log_err "System update failed after $MAX_RETRIES attempts. Aborting to ensure system stability."
    return 1
  fi

  # --------------------------------------------------------------------------
  # STEP 2: Filter Missing Packages
  # --------------------------------------------------------------------------
  log_info "Checking installation status..."
  
  local -a to_install=()
  
  # pacman -T returns 1 if packages are missing. || true prevents strict pipefail exit.
  # 2>/dev/null suppresses the "package not found" errors that pacman -T emits for AUR pkgs.
  if [[ ${#PACKAGES[@]} -gt 0 ]]; then
    mapfile -t to_install < <(pacman -T "${PACKAGES[@]}" 2>/dev/null || true)
  fi

  if [[ ${#to_install[@]} -eq 0 ]]; then
    log_success "All packages are already installed."
    return 0
  fi

  log_info "Packages to install: ${#to_install[@]}"
  
  # --------------------------------------------------------------------------
  # STEP 3: Batch Installation Strategy
  # --------------------------------------------------------------------------
  log_task "Attempting Batch Installation..."
  
  if "$AUR_HELPER" -S --needed --noconfirm "${to_install[@]}"; then
    log_success "Batch installation successful."
    return 0
  else
    log_warn "Batch installation failed. Switching to Granular Fallback Mode."
  fi

  # --------------------------------------------------------------------------
  # STEP 4: Granular Fallback Strategy
  # --------------------------------------------------------------------------
  local -a failed_pkgs=()
  local success_count=0
  local fail_count=0

  # Re-evaluate remaining packages (in case partial batch succeeded)
  local -a remaining=()
  mapfile -t remaining < <(pacman -T "${to_install[@]}" 2>/dev/null || true)

  if [[ ${#remaining[@]} -eq 0 ]]; then
    log_success "All packages installed during batch attempt."
    return 0
  fi

  local pkg retry_count user_input
  for pkg in "${remaining[@]}"; do
    # Skip empty entries from mapfile
    [[ -z "$pkg" ]] && continue

    log_task "Processing: $pkg"
    retry_count=0

    while true; do
      # 1. Try Auto Install
      if "$AUR_HELPER" -S --needed --noconfirm "$pkg"; then
        log_success "Installed $pkg."
        ((success_count++))
        break
      fi

      log_warn "Automatic install failed for $pkg."

      # 2. Check retry limit (Prevent Infinite Loop)
      if ((retry_count >= MAX_RETRIES)); then
        log_err "Max retries ($MAX_RETRIES) reached for $pkg. Skipping."
        ((fail_count++))
        failed_pkgs+=("$pkg")
        break
      fi

      # 3. Handle Non-Interactive Sessions (CI/Scripts)
      if [[ ! -t 0 ]]; then
        ((retry_count++))
        log_info "Non-interactive session. Retry $retry_count/$MAX_RETRIES in ${TIMEOUT_SEC}s..."
        sleep "$TIMEOUT_SEC"
        continue
      fi

      # 4. Interactive Intervention
      # The read command waits for TIMEOUT_SEC. If no input, it returns false (exit code 1)
      # and the script falls to the 'else' block, triggering the auto-retry.
      printf "%s  -> Manual install [M] or Skip [S]? (Auto-retry in %ss)... %s" \
        "${C_YELLOW}" "$TIMEOUT_SEC" "${C_RESET}"

      user_input=""
      # read -r for raw input safety
      if read -t "$TIMEOUT_SEC" -n 1 -r -s user_input; then
        case "${user_input,,}" in
          m)
            printf "\n"
            log_info "Switching to Manual Mode for $pkg..."
            # Manual mode: interactive (no --noconfirm)
            if "$AUR_HELPER" -S "$pkg"; then
              log_success "Manual install successful."
              ((success_count++))
              break
            else
              log_err "Manual install also failed."
              ((retry_count++))
            fi
            ;;
          s)
            printf "\n"
            log_warn "Skipping $pkg."
            ((fail_count++))
            failed_pkgs+=("$pkg")
            break
            ;;
          *)
            printf "\n"
            log_info "Invalid input. Please enter 'M' or 'S'."
            ((retry_count++))
            ;;
        esac
      else
        # TIMEOUT HIT: This effectively acts as the "sleep 5" because 'read' waited 5 seconds
        printf "\n"
        ((retry_count++))
        log_info "Timeout. Auto-retry $retry_count/$MAX_RETRIES..."
      fi
    done
  done

  # --------------------------------------------------------------------------
  # SUMMARY
  # --------------------------------------------------------------------------
  printf "\n"
  printf "%s========================================%s\n" "${C_BOLD}" "${C_RESET}"
  printf "%s     INSTALLATION SUMMARY              %s\n" "${C_BOLD}" "${C_RESET}"
  printf "%s========================================%s\n" "${C_BOLD}" "${C_RESET}"

  log_success "Successful: $success_count"

  if [[ $fail_count -gt 0 ]]; then
    log_err "Failed: $fail_count"
    log_err "The following packages failed to install:"
    local f
    for f in "${failed_pkgs[@]}"; do
      printf "   - %s\n" "$f"
    done
    return 1
  else
    log_success "All packages processed successfully."
    return 0
  fi
}

main "$@"
