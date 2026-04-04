#!/usr/bin/env bash
# To install AUR packages
# ==============================================================================
# Script Name: install_pkg_manifest.sh
# Description: Autonomous AUR/repo package installer with batch-first fallback.
# Context:     Arch Linux (Rolling) | Hyprland | UWSM
# ==============================================================================

# ------------------------------------------------------------------------------
# 1. STRICT SAFETY & SETTINGS
# ------------------------------------------------------------------------------
# -u: Treat unset variables as an error
# -o pipefail: Pipeline fails if any command fails
#
# Intentionally not using `set -e` because this script relies on controlled
# retries, fallbacks, and interactive recovery paths.
set -uo pipefail

# Global runtime state
declare -gi CAN_PROMPT=0
declare -gi TTY_FD=-1

# ------------------------------------------------------------------------------
# 2. VISUALS & LOGGING
# ------------------------------------------------------------------------------
# All human-oriented output goes to stderr so ordering stays consistent and
# stdout remains clean.
if [[ -t 2 ]]; then
  declare -gr C_RESET=$'\e[0m'
  declare -gr C_BOLD=$'\e[1m'
  declare -gr C_GREEN=$'\e[1;32m'
  declare -gr C_BLUE=$'\e[1;34m'
  declare -gr C_YELLOW=$'\e[1;33m'
  declare -gr C_RED=$'\e[1;31m'
  declare -gr C_CYAN=$'\e[1;36m'
else
  declare -gr C_RESET=''
  declare -gr C_BOLD=''
  declare -gr C_GREEN=''
  declare -gr C_BLUE=''
  declare -gr C_YELLOW=''
  declare -gr C_RED=''
  declare -gr C_CYAN=''
fi

log_info()    { printf '%s[INFO]%s %s\n'    "${C_BLUE}"   "${C_RESET}" "$*" >&2; }
log_success() { printf '%s[SUCCESS]%s %s\n' "${C_GREEN}"  "${C_RESET}" "$*" >&2; }
log_warn()    { printf '%s[WARN]%s %s\n'    "${C_YELLOW}" "${C_RESET}" "$*" >&2; }
log_err()     { printf '%s[ERROR]%s %s\n'   "${C_RED}"    "${C_RESET}" "$*" >&2; }
log_task()    { printf '\n%s%s:: %s%s\n'    "${C_BOLD}"   "${C_CYAN}"  "$*" "${C_RESET}" >&2; }

# ------------------------------------------------------------------------------
# 3. CLEANUP & TRAPS
# ------------------------------------------------------------------------------
disable_prompt_capability() {
  if (( TTY_FD >= 0 )); then
    exec {TTY_FD}>&- 2>/dev/null || :
    TTY_FD=-1
  fi
  CAN_PROMPT=0
}

cleanup() {
  disable_prompt_capability
  [[ -n "${C_RESET}" ]] && printf '%s' "${C_RESET}" >&2
}

abort_with_signal() {
  local signal_name=$1
  local exit_code=$2

  trap - EXIT INT TERM
  cleanup
  printf '\n' >&2
  log_err "Interrupted by ${signal_name}. Aborting."
  exit "${exit_code}"
}

trap cleanup EXIT
trap 'abort_with_signal SIGINT 130' INT
trap 'abort_with_signal SIGTERM 143' TERM

# ------------------------------------------------------------------------------
# 4. CONFIGURATION
# ------------------------------------------------------------------------------
# installing manually with script because this is a massive package that includes every cursor theme
# "bibata-cursor-theme-bin"

declare -ar PACKAGES=(
)

# Delay before auto-retrying
declare -ir TIMEOUT_SEC=5
# Total attempts per operation
declare -ir MAX_ATTEMPTS=6

# ------------------------------------------------------------------------------
# 5. PRE-FLIGHT CHECKS
# ------------------------------------------------------------------------------
require_command() {
  local cmd=$1

  if ! command -v "${cmd}" &>/dev/null; then
    log_err "Required command not found: ${cmd}"
    exit 1
  fi
}

detect_aur_helper() {
  local helper=''

  if command -v paru &>/dev/null; then
    helper='paru'
  elif command -v yay &>/dev/null; then
    helper='yay'
  else
    log_err "AUR helper (paru/yay) not found. Please install one first."
    exit 1
  fi

  declare -gr AUR_HELPER="${helper}"
}

detect_prompt_capability() {
  local fd=-1

  disable_prompt_capability

  if exec {fd}<>/dev/tty 2>/dev/null; then
    CAN_PROMPT=1
    TTY_FD=${fd}
  fi
}

validate_config() {
  if (( TIMEOUT_SEC <= 0 )); then
    log_err "TIMEOUT_SEC must be greater than 0."
    exit 1
  fi

  if (( MAX_ATTEMPTS <= 0 )); then
    log_err "MAX_ATTEMPTS must be greater than 0."
    exit 1
  fi
}

preflight_checks() {
  if (( EUID == 0 )); then
    log_err "This script must not be run as root."
    log_err "AUR helpers handle privileges internally."
    exit 1
  fi

  require_command pacman
  detect_aur_helper
  detect_prompt_capability
  validate_config
}

# ------------------------------------------------------------------------------
# 6. HELPERS
# ------------------------------------------------------------------------------
aur_install_auto() {
  "${AUR_HELPER}" -S --needed --noconfirm -- "$@"
}

aur_install_manual() {
  "${AUR_HELPER}" -S --needed -- "$@"
}

is_installed() {
  local pkg=$1
  # Utilizes pacman -T to correctly respect provides=() and virtual packages
  pacman -T "${pkg}" &>/dev/null
}

collect_uninstalled_packages() {
  local -n input_ref=$1
  local -n output_ref=$2

  output_ref=()

  if (( ${#input_ref[@]} == 0 )); then
    return 0
  fi

  # Optimized array processing: executes pacman once for the entire array
  mapfile -t output_ref < <(pacman -T "${input_ref[@]}" 2>/dev/null || true)
}

prompt_package_action() {
  local pkg=$1
  local -n action_ref=$2
  local user_input=''
  local -i deadline=$((SECONDS + TIMEOUT_SEC))
  local -i remaining=0

  action_ref='retry'

  while true; do
    remaining=$((deadline - SECONDS))
    (( remaining > 0 )) || return 0

    if ! printf '%s  -> %s failed. Manual install [M] or Skip [S]? (Auto-retry in %ss)... %s' \
      "${C_YELLOW}" "${pkg}" "${remaining}" "${C_RESET}" >&"${TTY_FD}"; then
      disable_prompt_capability
      return 0
    fi

    if IFS= read -r -n 1 -s -t "${remaining}" user_input <&"${TTY_FD}"; then
      printf '\n' >&"${TTY_FD}" 2>/dev/null || :

      case "${user_input,,}" in
        m)
          action_ref='manual'
          return 0
          ;;
        s)
          action_ref='skip'
          return 0
          ;;
        *)
          printf '%s[INFO]%s Invalid input. Press M or S.\n' \
            "${C_BLUE}" "${C_RESET}" >&"${TTY_FD}" 2>/dev/null || :
          ;;
      esac
    else
      printf '\n' >&"${TTY_FD}" 2>/dev/null || :
      return 0
    fi
  done
}

print_summary() {
  local -i total_requested=$1
  local -i fail_count=$2
  local -n failed_ref=$3
  local -i success_count=$((total_requested - fail_count))
  local pkg

  printf '\n' >&2
  printf '%s========================================%s\n' "${C_BOLD}" "${C_RESET}" >&2
  printf '%s     INSTALLATION SUMMARY              %s\n' "${C_BOLD}" "${C_RESET}" >&2
  printf '%s========================================%s\n' "${C_BOLD}" "${C_RESET}" >&2

  log_success "Successful: ${success_count}"

  if (( fail_count > 0 )); then
    log_err "Failed: ${fail_count}"
    log_err "The following packages failed to install:"
    for pkg in "${failed_ref[@]}"; do
      printf '   - %s\n' "${pkg}" >&2
    done
    return 1
  fi

  log_success "All packages processed successfully."
  return 0
}

# ------------------------------------------------------------------------------
# 7. MAIN LOGIC
# ------------------------------------------------------------------------------
main() {
  preflight_checks

  log_task "Starting Autonomous Package Installation Sequence"
  log_info "Using AUR Helper: ${AUR_HELPER}"
  log_info "Retry Policy: ${MAX_ATTEMPTS} total attempts | ${TIMEOUT_SEC}s delay"

  # --------------------------------------------------------------------------
  # STEP 1: Filter Missing Packages
  # --------------------------------------------------------------------------
  log_task "Checking installation status..."

  local -a to_install=()
  collect_uninstalled_packages PACKAGES to_install

  if (( ${#to_install[@]} == 0 )); then
    log_success "All packages are already installed."
    return 0
  fi

  local -i total_requested=${#to_install[@]}
  log_info "Packages to install: ${total_requested}"

  # --------------------------------------------------------------------------
  # STEP 2: Batch Installation Strategy
  # --------------------------------------------------------------------------
  log_task "Attempting Batch Installation..."

  if aur_install_auto "${to_install[@]}"; then
    local -a no_failures=()
    log_success "Batch installation successful."
    print_summary "${total_requested}" 0 no_failures
    return 0
  fi

  log_warn "Batch installation failed. Switching to granular fallback mode."

  # --------------------------------------------------------------------------
  # STEP 3: Granular Fallback Strategy
  # --------------------------------------------------------------------------
  local -a remaining=()
  local -a failed_pkgs=()
  local -i fail_count=0
  local -i attempt
  local pkg
  local action=''

  # Re-evaluate in case the batch attempt installed some packages before failing.
  collect_uninstalled_packages to_install remaining

  if (( ${#remaining[@]} == 0 )); then
    local -a no_failures=()
    log_success "All packages installed during the batch attempt."
    print_summary "${total_requested}" 0 no_failures
    return 0
  fi

  for pkg in "${remaining[@]}"; do
    [[ -n "${pkg}" ]] || continue

    log_task "Processing: ${pkg}"

    for (( attempt = 1; attempt <= MAX_ATTEMPTS; attempt++ )); do
      if aur_install_auto "${pkg}"; then
        log_success "Installed ${pkg}."
        break
      fi

      if is_installed "${pkg}"; then
        log_success "${pkg} is installed despite a non-zero helper exit status."
        break
      fi

      log_warn "Automatic install failed for ${pkg} (attempt ${attempt}/${MAX_ATTEMPTS})."

      if (( attempt == MAX_ATTEMPTS )); then
        log_err "Max attempts reached for ${pkg}. Skipping."
        failed_pkgs+=("${pkg}")
        (( fail_count++ ))
        break
      fi

      if (( CAN_PROMPT == 0 )); then
        log_info "Non-interactive session. Retrying in ${TIMEOUT_SEC}s..."
        sleep "${TIMEOUT_SEC}"
        continue
      fi

      prompt_package_action "${pkg}" action

      case "${action}" in
        manual)
          log_info "Switching to manual mode for ${pkg}..."
          if aur_install_manual "${pkg}"; then
            log_success "Manual install successful for ${pkg}."
            break
          fi

          if is_installed "${pkg}"; then
            log_success "${pkg} is installed despite a non-zero helper exit status."
            break
          fi

          log_err "Manual install failed for ${pkg}."
          ;;
        skip)
          log_warn "Skipping ${pkg}."
          failed_pkgs+=("${pkg}")
          (( fail_count++ ))
          break
          ;;
        retry)
          log_info "Timeout. Auto-retrying..."
          ;;
      esac
    done
  done

  print_summary "${total_requested}" "${fail_count}" failed_pkgs
}

main "$@"
