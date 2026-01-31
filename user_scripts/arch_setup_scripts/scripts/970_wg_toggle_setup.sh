#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Script: 970_wg_toggle_setup.sh
# Description: Clone/build wg-toggle into vpn folder and install the binary.
# Environment: Arch Linux | Bash 5+ | UWSM
# -----------------------------------------------------------------------------

# --- 1. Strict Mode & Safety ---
set -euo pipefail
IFS=$'\n\t'

# --- 2. Configuration & Visuals ---
readonly C_RESET=$'\033[0m'
readonly C_BOLD=$'\033[1m'
readonly C_GREEN=$'\033[1;32m'
readonly C_BLUE=$'\033[1;34m'
readonly C_RED=$'\033[1;31m'
readonly C_YELLOW=$'\033[1;33m'

readonly REPO_URL="https://github.com/T3rr0or/wg-toggle.git"
readonly VPN_DIR="$HOME/user_scripts/vpn"
readonly REPO_DIR="$VPN_DIR/wg-waybar-toggle"
readonly BIN_NAME="wg-toggle"
readonly BIN_DIR="$HOME/.local/bin"

# --- 3. Helper Functions ---
log_info() { printf "${C_BLUE}[INFO]${C_RESET} %s\n" "$1"; }
log_success() { printf "${C_GREEN}[OK]${C_RESET}   %s\n" "$1"; }
log_warn() { printf "${C_YELLOW}[WARN]${C_RESET} %s\n" "$1"; }
log_error() { printf "${C_RED}[ERR]${C_RESET}  %s\n" "$1" >&2; }

check_dependencies() {
    local deps=(git cargo)
    for dep in "${deps[@]}"; do
        if ! command -v "$dep" >/dev/null 2>&1; then
            log_error "Missing dependency: $dep"
            return 1
        fi
    done
}

clone_or_update_repo() {
    mkdir -p "$VPN_DIR"

    if [[ -d "$REPO_DIR/.git" ]]; then
        log_info "Repository exists. Pulling latest changes..."
        git -C "$REPO_DIR" pull --ff-only
    else
        log_info "Cloning wg-toggle into $REPO_DIR..."
        git clone "$REPO_URL" "$REPO_DIR"
    fi
}

build_binary() {
    log_info "Building release binary..."
    (cd "$REPO_DIR" && cargo build --release)

    if [[ ! -f "$REPO_DIR/target/release/$BIN_NAME" ]]; then
        log_error "Build succeeded but binary not found: $REPO_DIR/target/release/$BIN_NAME"
        exit 1
    fi
}

install_binary() {
    mkdir -p "$BIN_DIR"
    log_info "Installing binary to $BIN_DIR/$BIN_NAME..."
    install -Dm755 "$REPO_DIR/target/release/$BIN_NAME" "$BIN_DIR/$BIN_NAME"
}

main() {
    printf "${C_BOLD}wg-toggle Setup${C_RESET}\n"

    if ! check_dependencies; then
        log_error "Install missing dependencies and re-run."
        exit 1
    fi

    clone_or_update_repo
    build_binary
    install_binary

    log_success "wg-toggle built and installed successfully."
    log_success "Location: $BIN_DIR/$BIN_NAME"
}

main "$@"
