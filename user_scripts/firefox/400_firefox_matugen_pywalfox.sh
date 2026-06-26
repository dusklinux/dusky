#!/usr/bin/env bash
# firefox setup for matugen themeing
# -----------------------------------------------------------------------------
# Script: 044_firefox_pywal.sh
# Description: Setup Firefox, Pywalfox, and Matugen (Orchestra compatible)
# Environment: Arch Linux / Hyprland / UWSM
# -----------------------------------------------------------------------------

# --- Safety & Error Handling ---
set -euo pipefail
IFS=$'\n\t'
trap 'printf "\n[WARN] Script interrupted. Exiting.\n" >&2; exit 130' INT TERM

# --- Configuration ---
readonly BROWSER_BIN='firefox'
readonly NATIVE_HOST_PKG='python-pywalfox'
readonly THEME_ENGINE_PKG='matugen'

# Autonomous Extension Configs
# Using Mozilla's official dynamic routing endpoint for enterprise policies
readonly XPI_URL="https://addons.mozilla.org/firefox/downloads/latest/pywalfox/latest.xpi"
readonly FIREFOX_ROOT="/usr/lib/firefox"
readonly FIREFOX_DIST="${FIREFOX_ROOT}/distribution"
readonly POLICIES_FILE="${FIREFOX_DIST}/policies.json"

# --- Visual Styling ---
if command -v tput &>/dev/null && (( $(tput colors 2>/dev/null || echo 0) >= 8 )); then
    readonly C_RESET=$'\033[0m'
    readonly C_BOLD=$'\033[1m'
    readonly C_BLUE=$'\033[38;5;45m'
    readonly C_GREEN=$'\033[38;5;46m'
    readonly C_MAGENTA=$'\033[38;5;177m'
    readonly C_WARN=$'\033[38;5;214m'
    readonly C_ERR=$'\033[38;5;196m'
else
    readonly C_RESET='' C_BOLD='' C_BLUE='' C_GREEN=''
    readonly C_MAGENTA='' C_WARN='' C_ERR=''
fi

# --- Logging Utilities ---
log_info()    { printf '%b[INFO]%b %s\n' "${C_BLUE}" "${C_RESET}" "$1"; }
log_success() { printf '%b[SUCCESS]%b %s\n' "${C_GREEN}" "${C_RESET}" "$1"; }
log_warn()    { printf '%b[WARNING]%b %s\n' "${C_WARN}" "${C_RESET}" "$1" >&2; }
die()         { printf '%b[ERROR]%b %s\n' "${C_ERR}" "${C_RESET}" "$1" >&2; exit 1; }

# --- Helper Functions ---
check_aur_helper() {
    if command -v paru &>/dev/null; then echo "paru";
    elif command -v yay &>/dev/null; then echo "yay";
    else return 1; fi
}

preflight() {
    if ((EUID == 0)); then die 'Run as normal user, not Root.'; fi
}

show_help() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Automated provisioning for Firefox, Matugen, and the Pywalfox ecosystem via Enterprise Policies.

Options:
  -h, --help       Show this help message and exit.
  --ext-only       Skip package checks and native host rebuilding. Only reinstalls/updates 
                   the Pywalfox Firefox extension policy to pull the latest version.
EOF
}

# --- Core Modules ---

install_core_packages() {
    log_info "Ensuring ${BROWSER_BIN} and ${THEME_ENGINE_PKG} are installed..."
    if sudo pacman -S --needed --noconfirm "${BROWSER_BIN}" "${THEME_ENGINE_PKG}"; then
        log_success "Core packages verified."
    else
        die "Failed to install standard packages."
    fi
}

install_native_backend() {
    log_info "Handling ${NATIVE_HOST_PKG}..."
    local helper
    if helper=$(check_aur_helper); then
        # Idempotent cleanup
        if pacman -Qq "${NATIVE_HOST_PKG}" &>/dev/null; then
            log_warn "Existing ${NATIVE_HOST_PKG} found. Removing to enforce clean rebuild..."
            sudo pacman -Rns --noconfirm "${NATIVE_HOST_PKG}" || true
        fi

        log_info "Installing/Rebuilding ${NATIVE_HOST_PKG} with ${helper}..."
        if "$helper" -S --rebuild --noconfirm "${NATIVE_HOST_PKG}"; then
            log_success "${NATIVE_HOST_PKG} ready."
            
            if command -v pywalfox &>/dev/null; then
                # 0-Day Patch: Ensure the user's mozilla directory exists before manifest registration
                log_info "Initializing Mozilla directories for 0-day setup..."
                mkdir -p "${HOME}/.mozilla/native-messaging-hosts"
                
                log_info "Refreshing manifest..."
                pywalfox install || log_warn "Manifest update failed (non-fatal)."
            fi
        else
            die "Failed to install ${NATIVE_HOST_PKG}."
        fi
    else
        log_warn "No AUR helper found. Skipping Pywalfox backend."
    fi
}

deploy_extension_policy() {
    log_info "Deploying Pywalfox Enterprise Extension Policy..."
    
    if [[ ! -d "$FIREFOX_ROOT" ]]; then
        die "Firefox root not found at $FIREFOX_ROOT. Installation may be corrupted."
    fi

    # Clean up any legacy, root-owned physical files from previous script iterations
    if [[ -f "${FIREFOX_ROOT}/extensions/pywalfox.xpi" ]]; then
        log_warn "Cleaning up legacy root-owned extension file..."
        sudo rm -f "${FIREFOX_ROOT}/extensions/pywalfox.xpi"
    fi

    # Create the distribution directory and ensure strict permissions
    sudo mkdir -p "$FIREFOX_DIST"
    sudo chmod 755 "$FIREFOX_DIST"

    # Write the policy payload
    # 'normal_installed' acts autonomously but prevents the "isManaged" GUI lockout
    sudo tee "$POLICIES_FILE" > /dev/null <<EOF
{
  "policies": {
    "ExtensionSettings": {
      "*": {
        "installation_mode": "allowed"
      },
      "pywalfox@frewacom.org": {
        "installation_mode": "normal_installed",
        "install_url": "${XPI_URL}"
      }
    }
  }
}
EOF

    sudo chmod 644 "$POLICIES_FILE"
    log_success "Enterprise policy applied."
}

finish_setup() {
    hash -r 2>/dev/null || true
    if [[ -t 1 ]]; then clear; fi

    printf '%b%b' "${C_BOLD}" "${C_BLUE}"
    cat <<'BANNER'
   ╔═══════════════════════════════════════╗
   ║      PYWALFOX SETUP COMPLETED         ║
   ║      Arch / Hyprland / UWSM           ║
   ╚═══════════════════════════════════════╝
BANNER
    printf '%b\n' "${C_RESET}"
    log_success "Zero-touch setup finished successfully."
    log_info "The extension will automatically install the first time you open Firefox."
}

# --- Main Logic Execution ---
main() {
    preflight

    # Argument Parsing
    local ext_only=0
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -h|--help)
                show_help
                exit 0
                ;;
            --ext-only)
                ext_only=1
                shift
                ;;
            *)
                log_warn "Unknown option: $1"
                show_help
                exit 1
                ;;
        esac
    done

    printf '\n%b>>> AUTOMATED SETUP: FIREFOX, PYWALFOX & MATUGEN%b\n' "${C_BLUE}" "${C_RESET}"

    if (( ext_only == 1 )); then
        log_info "Running in EXTENSION-ONLY mode..."
        deploy_extension_policy
    else
        log_info "Running FULL autonomous 0-day installation..."
        install_core_packages
        install_native_backend
        deploy_extension_policy
    fi

    finish_setup
}

main "$@"
