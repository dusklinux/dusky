#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Script: setup_dotfiles.sh
# Description: Bootstraps dotfiles using a bare git repository method.
# Target: Arch Linux / Hyprland / UWSM environment
# Author: Elite DevOps Engineer
# -----------------------------------------------------------------------------

# strict mode: exit on error, undefined vars, or pipe failures
set -euo pipefail

# -----------------------------------------------------------------------------
# Constants & Configuration
# -----------------------------------------------------------------------------
readonly REPO_URL="https://github.com/dusklinux/dusky"
readonly DOTFILES_DIR="${HOME}/dusky"
readonly GIT_EXEC="/usr/bin/git"

# ANSI Color Codes for modern, readable output
readonly C_RESET='\033[0m'
readonly C_INFO='\033[1;34m'    # Bold Blue
readonly C_SUCCESS='\033[1;32m' # Bold Green
readonly C_ERROR='\033[1;31m'   # Bold Red
readonly C_WARN='\033[1;33m'    # Bold Yellow

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
log_info() {
    printf "${C_INFO}[INFO]${C_RESET} %s\n" "$1"
}

log_success() {
    printf "${C_SUCCESS}[OK]${C_RESET} %s\n" "$1"
}

log_warn() {
    printf "${C_WARN}[WARN]${C_RESET} %s\n" "$1"
}

log_error() {
    printf "${C_ERROR}[ERROR]${C_RESET} %s\n" "$1" >&2
}

# Cleanup function to be trapped on exit
cleanup() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        log_error "Script failed with exit code $exit_code."
    fi
}
trap cleanup EXIT

# -----------------------------------------------------------------------------
# Main Execution
# -----------------------------------------------------------------------------
main() {
    local force_mode=0

    # Parse arguments
    for arg in "$@"; do
        if [[ "$arg" == "--force" ]] || [[ "$arg" == "-f" ]]; then
            force_mode=1
            break
        fi
    done

    # 1. Pre-flight Checks
    if ! command -v git &> /dev/null; then
        log_error "Git is not installed. Please run 'pacman -S git' first."
        exit 1
    fi

    # --- SAFETY INTERLOCK START ---
    if [[ $force_mode -eq 0 ]]; then
        printf "\n"
        printf "${C_WARN}!!! CRITICAL WARNING !!!${C_RESET}\n"
        printf "${C_WARN}This script will FORCE OVERWRITE existing configuration files in %s.${C_RESET}\n" "$HOME"
        printf "${C_WARN}All custom changes will be lost permanently.${C_RESET}\n"
        printf "${C_WARN}NOTE: 'Orchestra' must be rerun after this process completes to finalize setup.${C_RESET}\n"
        printf "\n"
        
        read -r -p "Are you sure you want to proceed? [y/N] " response
        if [[ ! "$response" =~ ^[yY]([eE][sS])?$ ]]; then
            log_info "Operation aborted by user."
            exit 0
        fi
        printf "\n"
    else
        log_warn "Running in autonomous mode (--force). Bypassing safety prompts."
    fi
    # --- SAFETY INTERLOCK END ---

    log_info "Starting dotfiles bootstrap for user: $USER"
    log_info "Target Directory: $DOTFILES_DIR"

    # Clean up existing directory to ensure a fresh clone
    rm -rf "$DOTFILES_DIR"

    # 2. Clone the Bare Repository
    log_info "Cloning bare repository..."
    if "$GIT_EXEC" clone --bare --depth 1 "$REPO_URL" "$DOTFILES_DIR"; then
        log_success "Repository cloned successfully."
    else
        log_error "Failed to clone repository."
        exit 1
    fi

    # -------------------------------------------------------------------------
    # ITERATIVE BACKUP LOGIC FOR edit_here
    # -------------------------------------------------------------------------
    local edit_target="${HOME}/.config/hypr/edit_here"
    
    if [[ -d "$edit_target" ]]; then
        local counter=1
        local backup_path="${edit_target}.${counter}.bak"

        # Increment counter until an available backup path is found
        while [[ -e "$backup_path" ]]; do
            ((counter++))
            backup_path="${edit_target}.${counter}.bak"
        done

        log_info "Found existing ${edit_target}. Moving to iterative backup..."
        
        # Using mv to rename the directory, achieving a backup and removal in one atomic step
        if mv "$edit_target" "$backup_path"; then
            log_success "Successfully moved and backed up to ${backup_path}"
        else
            log_error "Failed to move/backup ${edit_target}. Proceeding anyway."
        fi
    fi
    # -------------------------------------------------------------------------

    # 3. Checkout Files
    log_info "Checking out configuration files to $HOME..."
    log_info "NOTE: This will overwrite existing files (forced checkout)."

    if "$GIT_EXEC" --git-dir="$DOTFILES_DIR/" --work-tree="$HOME" checkout -f; then
        log_success "Dotfiles checked out successfully."
    else
        log_error "Checkout failed. You may have conflicting files that git cannot overwrite despite -f."
        exit 1
    fi

    # 4. Completion
    log_success "Setup complete. Your Hyprland/UWSM environment is ready."
    log_info "REMINDER: Please rerun Orchestra now."
}

# Invoke main and pass all script arguments to it
main "$@"
