#!/usr/bin/env bash
# Installs nveovim plugins based on the lua files in .config/nvim
# ==============================================================================
#  NEOVIM PLUGIN SYNCHRONIZER (HEADLESS)
# ==============================================================================
#  Context: Arch Linux / Hyprland / UWSM
#  Purpose: Bootstraps and syncs Lazy.nvim plugins efficiently
# ==============================================================================

# 1. Safety & Strict Mode
set -euo pipefail

# 2. Visual Formatting (ANSI-C Quoting for safety)
declare -r GREEN=$'\033[0;32m'
declare -r BLUE=$'\033[0;34m'
declare -r RED=$'\033[0;31m'
declare -r YELLOW=$'\033[0;33m'
declare -r BOLD=$'\033[1m'
declare -r RESET=$'\033[0m'

# Note: We pass colors as arguments to printf to avoid format string injection
log_info() { printf "%s[INFO]%s %s\n" "${BLUE}" "${RESET}" "$1"; }
log_success() { printf "%s[SUCCESS]%s %s\n" "${GREEN}" "${RESET}" "$1"; }
log_warn() { printf "%s[WARN]%s %s\n" "${YELLOW}" "${RESET}" "$1"; }
log_error() { printf "%s[ERROR]%s %s\n" "${RED}" "${RESET}" "$1"; }

# 3. Cleanup Trap
cleanup() {
    # No temporary files to remove, but ensures clean exit signal
    :
}
trap cleanup EXIT

# 4. Main Logic
main() {
    log_info "Initializing Neovim Plugin Synchronization..."

    # --- Pre-flight Check: Binaries ---
    if ! command -v nvim &>/dev/null; then
        log_error "Neovim (nvim) is not installed or not in PATH."
        exit 1
    fi
    
    if ! command -v git &>/dev/null; then
        log_error "Git is not installed. Lazy.nvim requires git."
        exit 1
    fi

    # --- Pre-flight Check: Configuration ---
    if [[ ! -d "${HOME}/.config/nvim" ]]; then
        log_error "Neovim configuration directory (${HOME}/.config/nvim) not found."
        log_warn "Please ensure dotfiles are symlinked/copied before running this script."
        exit 1
    fi

    # --- Pre-flight Check: Network ---
    log_info "Verifying connectivity..."
    if ! ping -c 1 github.com &>/dev/null; then
        log_error "Cannot reach GitHub. Network is unreachable."
        exit 1
    fi

    # --- Execution ---
    log_info "Starting Headless Sync. This may take a moment..."
    log_warn "Output from Neovim will be shown below:"
    echo "--------------------------------------------------------------------------------"
    
    # +Lazy! sync runs the sync/update
    # +qa quits all windows after the command finishes
    if nvim --headless "+Lazy! sync" +qa; then
        echo "--------------------------------------------------------------------------------"
        log_success "Neovim plugins synced successfully."
    else
        echo "--------------------------------------------------------------------------------"
        log_error "Neovim exited with an error code."
        exit 1
    fi
}

main
