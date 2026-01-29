#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Script: auto-spicetify.sh
# Description: Automated Spicetify setup/recovery for Dusky Dotfiles.
#              Handles package installation, marketplace injection, and 
#              Comfy theme setup while respecting UWSM/Matugen environments.
# Author: Dusky Dotfiles Automation
# License: MIT
# -----------------------------------------------------------------------------

# Strict Mode:
# -e: Exit on error
# -u: Exit on unset variable
# -o pipefail: Exit if any command in a pipe fails
# -E: Inherit ERR traps in functions
set -Eeuo pipefail

# --- Configuration ---
readonly SCRIPT_NAME="${0##*/}"
readonly REQUIRED_BASH_VERSION=5
# Temporary file tracker
declare -a TEMP_FILES=()

# --- Visual Feedback (ANSI C Quoting) ---
if [[ -t 1 ]]; then
    readonly COLOR_RESET=$'\033[0m'
    readonly COLOR_INFO=$'\033[1;34m'    # Blue
    readonly COLOR_SUCCESS=$'\033[1;32m' # Green
    readonly COLOR_WARN=$'\033[1;33m'    # Yellow
    readonly COLOR_ERR=$'\033[1;31m'     # Red
    readonly COLOR_BOLD=$'\033[1m'
else
    readonly COLOR_RESET=''
    readonly COLOR_INFO=''
    readonly COLOR_SUCCESS=''
    readonly COLOR_WARN=''
    readonly COLOR_ERR=''
    readonly COLOR_BOLD=''
fi

# --- Logging Functions ---
log_info()    { printf '%s[INFO]%s %s\n' "${COLOR_INFO}" "${COLOR_RESET}" "$*"; }
log_success() { printf '%s[OK]%s %s\n' "${COLOR_SUCCESS}" "${COLOR_RESET}" "$*"; }
log_warn()    { printf '%s[WARN]%s %s\n' "${COLOR_WARN}" "${COLOR_RESET}" "$*" >&2; }
log_err()     { printf '%s[ERROR]%s %s\n' "${COLOR_ERR}" "${COLOR_RESET}" "$*" >&2; }
die()         { log_err "$*"; exit 1; }

# --- Cleanup Handler ---
cleanup() {
    local exit_code=$?
    trap - EXIT INT TERM

    # Remove temp files
    for file in "${TEMP_FILES[@]}"; do
        if [[ -f "$file" ]]; then
            rm -f "$file"
        fi
    done

    if [[ $exit_code -ne 0 ]]; then
        log_err "Script failed with exit code $exit_code"
    fi
    exit "$exit_code"
}
trap cleanup EXIT INT TERM

# --- Dependency Management ---

# Function: Detect Package Manager
# Priority: pacman (if package in repos) -> paru -> yay
detect_pm() {
    # Check if spicetify-cli is available via pacman (e.g. chaotic-aur)
    if command -v pacman &>/dev/null && pacman -Si spicetify-cli &>/dev/null; then
        echo "pacman"
        return 0
    fi

    if command -v paru &>/dev/null; then
        echo "paru"
        return 0
    fi

    if command -v yay &>/dev/null; then
        echo "yay"
        return 0
    fi

    die "No suitable package manager found. Please install paru or yay."
}

# Function: Install Package
install_package() {
    local pkg="$1"
    local pm
    pm=$(detect_pm)

    log_info "Installing $pkg using $pm..."
    
    case "$pm" in
        pacman)
            sudo pacman -S --needed --noconfirm "$pkg"
            ;;
        paru|yay)
            "$pm" -S --needed --noconfirm "$pkg"
            ;;
    esac
}

# --- Main Logic ---

check_requirements() {
    # 1. Bash Version Check
    if ((BASH_VERSINFO[0] < REQUIRED_BASH_VERSION)); then
        die "Bash 5.0+ required. Current: $BASH_VERSION"
    fi

    # 2. Check for Spotify
    if command -v spotify &>/dev/null; then
        log_success "Spotify binary detected."
    elif command -v spotify-launcher &>/dev/null; then
        log_success "Spotify-launcher detected."
    else
        die "Spotify is not installed! Install 'spotify' or 'spotify-launcher' first."
    fi
}

prompt_user_confirmation() {
    # Skip prompt if --yes or -y passed
    if [[ "${1:-}" == "--yes" || "${1:-}" == "-y" ]]; then
        log_info "Auto-confirm enabled."
        return 0
    fi

    log_warn "--- USER ATTENTION REQUIRED ---"
    printf "%s" "To ensure Spicetify works, please confirm:
  1. Spotify is installed and you are logged in.
  2. You have kept Spotify open for ~60 seconds (to generate config files).
"
    
    while true; do
        printf "${COLOR_BOLD}Ready to proceed? [y/n]: ${COLOR_RESET}"
        read -r -p "" confirm || confirm="n"
        case "${confirm,,}" in
            y|yes) break ;;
            n|no)  die "Setup aborted by user." ;;
            *)     log_warn "Please answer 'y' or 'n'." ;;
        esac
    done
}

setup_spicetify() {
    # Install CLI
    if ! command -v spicetify &>/dev/null; then
        install_package "spicetify-cli"
    else
        log_info "Spicetify CLI is already installed."
    fi

    # Initialize Config
    log_info "Generating Spicetify config..."
    # Running 'spicetify' generates config if missing, but prints help if present.
    # We silence output (> /dev/null) to avoid the long help text cluttering logs.
    spicetify > /dev/null 2>&1 || true

    # Backup & Inject
    # We allow this to fail (returns non-zero if backup already exists)
    log_info "Applying backup and enabling devtools..."
    if ! spicetify backup apply enable-devtools 2>/dev/null; then
        log_warn "Backup/Apply returned non-zero. Assuming Spotify is already patched or backup exists."
        log_info "Proceeding with update..."
    else
        log_success "Backup and injection successful."
    fi

    # Update Extensions
    log_info "Updating internal extensions..."
    spicetify update
}

install_marketplace() {
    log_info "Installing Spicetify Marketplace..."
    
    # Create temp file for script
    local mk_script
    mk_script=$(mktemp)
    TEMP_FILES+=("$mk_script")

    # Download
    if ! curl -fsSL "https://raw.githubusercontent.com/spicetify/spicetify-marketplace/main/resources/install.sh" -o "$mk_script"; then
        die "Failed to download Marketplace installer."
    fi

    # Execute
    if ! bash "$mk_script"; then
        log_warn "Marketplace install script returned error. It might already be installed."
    else
        log_success "Marketplace installed."
    fi
}

setup_theme() {
    local config_dir
    # Safer dirname handling
    config_dir="$(dirname "$(spicetify -c)")"
    
    local themes_dir="$config_dir/Themes"
    local comfy_dir="$themes_dir/Comfy"

    log_info "Setting up Comfy Theme..."
    mkdir -p "$themes_dir"

    if [[ -d "$comfy_dir" ]]; then
        # CRITICAL: Check for Matugen symlink
        if [[ -L "$comfy_dir/color.ini" ]]; then
            log_success "Matugen configuration detected (color.ini is a symlink)."
            log_info "Skipping Git pull to protect your generated colors."
        else
            log_info "Updating Comfy theme..."
            # Use --ff-only to prevent merge conflicts if user modified files
            if ! git -C "$comfy_dir" pull --ff-only; then
                log_warn "Git pull failed (likely local changes). Skipping update."
            fi
        fi
    else
        log_info "Cloning Comfy theme..."
        git clone https://github.com/Comfy-Themes/Spicetify "$comfy_dir"
    fi

    # Configure
    log_info "Configuring theme (Comfy)..."
    spicetify config current_theme Comfy color_scheme Comfy

    # Check if color.ini exists (Comfy theme should have it in the repo)
    if [[ ! -f "$comfy_dir/color.ini" ]]; then
        log_warn "color.ini not found in Comfy theme. Run 'matugen' to generate colors."
        log_info "Skipping spicetify apply for now."
        return 0
    fi

    # Apply
    log_info "Applying changes (Fast Reload)..."
    spicetify apply -n
}

# --- Execution ---

main() {
    check_requirements
    prompt_user_confirmation "${1:-}"
    setup_spicetify
    install_marketplace
    setup_theme

    echo ""
    log_success "Spicetify setup complete!"
    log_info "If colors are missing, run 'matugen' to generate them."
}

main "$@"