#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Script: 05-generate-initramfs.sh
# Description: Generates initramfs images using mkinitcpio.
# Context: Arch Linux Installation (Chroot/ISO)
# Author: Elite DevOps Engineer
# -----------------------------------------------------------------------------

# --- Strict Error Handling ---
set -euo pipefail

# --- Modern Bash Configuration ---
# Allow ** globs if needed later, though not strictly used here.
shopt -s globstar

# --- Visual Formatting ---
readonly C_RESET=$'\033[0m'
readonly C_BOLD=$'\033[1m'
readonly C_GREEN=$'\033[32m'
readonly C_YELLOW=$'\033[33m'
readonly C_RED=$'\033[31m'
readonly C_CYAN=$'\033[36m'

# --- Logging Functions ---
log_info() {
    printf "${C_BOLD}${C_CYAN}[INFO]${C_RESET} %s\n" "$1"
}

log_success() {
    printf "${C_BOLD}${C_GREEN}[OK]${C_RESET} %s\n" "$1"
}

log_warn() {
    printf "${C_BOLD}${C_YELLOW}[WARN]${C_RESET} %s\n" "$1"
}

log_error() {
    printf "${C_BOLD}${C_RED}[ERROR]${C_RESET} %s\n" "$1" >&2
}

# --- Trap / Cleanup ---
cleanup() {
    # Reset colors or cursor if script is interrupted
    printf "${C_RESET}"
}
trap cleanup EXIT INT TERM

# --- Main Logic ---
main() {
    log_info "Initializing initramfs generation process..."

    # Check for binary existence to avoid obscure failures
    if ! command -v mkinitcpio &> /dev/null; then
        log_error "mkinitcpio binary not found. Are you correctly chrooted?"
        exit 1
    fi

    log_info "Executing: mkinitcpio -P"
    log_info "Please wait. This may take a moment..."
    printf "%s\n" "----------------------------------------"

    # -------------------------------------------------------------------------
    # Execution Note:
    # We append '|| true' because 'mkinitcpio' frequently emits non-zero
    # exit codes for missing firmware (e.g., aic94xx, wd719x) which are
    # usually benign warnings on modern hardware.
    #
    # Since 'set -e' is active, a non-zero exit would normally crash the script.
    # The user explicitly stated these errors can be safely ignored.
    # -------------------------------------------------------------------------
    
    mkinitcpio -P || {
        printf "%s\n" "----------------------------------------"
        log_warn "mkinitcpio returned a non-zero exit code."
        log_warn "Proceeding as requested (ignoring potential firmware warnings)."
        return 0
    }

    printf "%s\n" "----------------------------------------"
    log_success "Initramfs generation complete."
}

# --- Execution ---
main
