#!/usr/bin/env bash
# Kokoro CPU setup
# install_kokoros_v2.sh
# -----------------------------------------------------------------------------
# Elite DevOps Setup for Kokoros (CPU/Rust/ONNX) on Arch/Hyprland/UWSM.
#
# Changes in v2:
# - OPTIMIZED: Forces PyTorch CPU-only installation (saves ~2.5GB storage).
# - Retains strict UWSM and cleanup logic.
# -----------------------------------------------------------------------------

set -euo pipefail

# --- Visuals & Logging ---
R=$'\033[0;31m'
G=$'\033[0;32m'
Y=$'\033[1;33m'
B=$'\033[0;34m'
NC=$'\033[0m' # No Color

log_info()    { printf "${B}[INFO]${NC} %s\n" "$1"; }
log_success() { printf "${G}[OK]${NC}   %s\n" "$1"; }
log_warn()    { printf "${Y}[WARN]${NC} %s\n" "$1"; }
log_error()   { printf "${R}[ERR]${NC}  %s\n" "$1" >&2; }

# --- Cleanup Trap ---
cleanup() {
    # Reset cursor if we hid it
    tput cnorm
    if [[ $? -ne 0 ]]; then
        log_error "Script failed or interrupted. Please check the output above."
    fi
}
trap cleanup EXIT

# --- Root & Environment Checks ---

if [[ $EUID -eq 0 ]]; then
    log_error "Do NOT run this script as root/sudo directly."
    log_error "Run it as your normal user. The script will ask for sudo when needed."
    exit 1
fi

log_info "Privilege check: Sudo access required for dependencies."
if ! sudo -v; then
    log_error "Sudo authentication failed."
    exit 1
fi

# Keep sudo alive in background
( while true; do sudo -n true; sleep 60; kill -0 "$$" || exit; done 2>/dev/null ) &

# --- Interactive Prompt ---

clear
printf "${B}
  _  __     _                        
 | |/ /    | |                       
 | ' / ___ | | __ ___  _ __ ___  ___ 
 |  < / _ \| |/ // _ \| '__/ _ \/ __|
 | . \ (_) |   <| (_) | | | (_) \__ \\
 |_|\_\___/|_|\_\\\\___/|_|  \___/|___/
                                     
${NC}"
printf "Arch Linux | Hyprland | UWSM | CPU-Optimized TTS\n\n"

log_info "This script will install the Kokoros TTS model (English)."
log_info "Target: CPU Inference (Rust/ONNX)."
printf "\n"
log_warn "HARDWARE REQUIREMENTS & WARNINGS:"
printf "  • ${Y}Storage:${NC} Requires ~2.5GB (Optimized from 5GB).\n"
printf "  • ${Y}Time:${NC}    Compilation takes 15-20 minutes (CPU dependent).\n"
printf "  • ${Y}GPU:${NC}     Using CPU-only PyTorch build (No Nvidia bloat).\n"
printf "\n"

read -p "Do you want to proceed with the installation? [y/N] " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    log_info "Installation aborted by user."
    exit 0
fi

# --- Phase 1: Dependencies ---

log_info "Installing system dependencies via Pacman..."
sudo pacman -S --needed --noconfirm git rust cargo unzip

# Check if uv is installed
if ! command -v uv &>/dev/null; then
    log_info "Installing 'uv' python manager..."
    sudo pacman -S --needed --noconfirm uv
fi

log_info "Checking for conflicting packages (espeak-ng, wordbook)..."
if pacman -Qs espeak-ng > /dev/null || pacman -Qs wordbook > /dev/null; then
    log_warn "Removing espeak-ng and wordbook to prevent build failures..."
    sudo pacman -Rns --noconfirm espeak-ng wordbook || true
else
    log_success "No conflicting packages found."
fi

# --- Phase 2: Workspace Setup ---

APP_DIR="$HOME/contained_apps/uv"
VENV_NAME="kokoros_cpu"
REPO_DIR="$APP_DIR/$VENV_NAME/Kokoros"

log_info "Setting up workspace at $APP_DIR..."
mkdir -p "$APP_DIR"
cd "$APP_DIR"

if [[ -d "$VENV_NAME" ]]; then
    log_warn "Directory $VENV_NAME already exists."
else
    log_info "Creating virtual environment ($VENV_NAME)..."
    uv venv "$VENV_NAME"
fi

# shellcheck source=/dev/null
source "$VENV_NAME/bin/activate"

cd "$VENV_NAME"

if [[ -d "Kokoros" ]]; then
    log_info "Repository already cloned. Pulling latest changes..."
    cd Kokoros
    git pull
else
    log_info "Cloning Kokoros repository..."
    git clone https://github.com/lucasjinreal/Kokoros.git
    cd Kokoros
fi

# --- Phase 3: Python Setup (OPTIMIZED) ---

log_info "Installing Python dependencies with uv..."

# [OPTIMIZATION] Force install CPU-only PyTorch first.
# This prevents downloading 2GB of Nvidia CUDA libs referenced by the default pip index.
log_info "Fetching CPU-optimized PyTorch (skipping CUDA bloat)..."
uv pip install torch --index-url https://download.pytorch.org/whl/cpu

# Now install the rest. uv will see 'torch' is already satisfied and skip the heavy GPU version.
log_info "Installing remaining requirements..."
uv pip install -r scripts/requirements.txt

# --- Phase 4: Build (The Heavy Lift) ---

log_info "Building release binary with Cargo..."
log_warn "This step takes 15-20 minutes. Please be patient."

if cargo build --release; then
    log_success "Compilation complete!"
else
    log_error "Compilation failed."
    exit 1
fi

# Verify build
if [[ -f "./target/release/koko" ]]; then
    ./target/release/koko -h > /dev/null 2>&1
    log_success "Binary verified."
else
    log_error "Binary not found after build."
    exit 1
fi

# --- Phase 5: Model Downloads ---

log_info "Preparing download scripts..."
chmod u+x scripts/download_models.sh scripts/download_voices.sh

log_info "Downloading Models..."
./scripts/download_models.sh

log_info "Downloading Voices..."
./scripts/download_voices.sh

# --- Phase 6: System Integration (UWSM) ---

log_info "Setting up symbolic link..."
mkdir -p "$HOME/.local/bin/"
ln -nfs "$REPO_DIR/target/release/koko" "$HOME/.local/bin/kokoros"
log_success "Linked: $HOME/.local/bin/kokoros -> koko"

UWSM_ENV="$HOME/.config/uwsm/env-hyprland"
PATH_EXPORT='export PATH="$HOME/.local/bin:$PATH"'

if [[ ! -f "$UWSM_ENV" ]]; then
    mkdir -p "$(dirname "$UWSM_ENV")"
    touch "$UWSM_ENV"
fi

if grep -Fxq "$PATH_EXPORT" "$UWSM_ENV"; then
    log_success "UWSM path already configured."
else
    log_info "Adding ~/.local/bin to UWSM env-hyprland..."
    [[ -s "$UWSM_ENV" && -n "$(tail -c 1 "$UWSM_ENV")" ]] && echo "" >> "$UWSM_ENV"
    echo "$PATH_EXPORT" >> "$UWSM_ENV"
    log_success "UWSM config updated."
fi

# --- Phase 7: Final Trigger ---

log_info "Triggering initial run..."
if "$HOME/.local/bin/kokoros" -h &>/dev/null; then
    log_success "Kokoros initialized successfully."
else
    log_error "Kokoros failed to initialize on first run."
fi

echo ""
echo "--------------------------------------------------------"
log_success "Installation Complete (CPU Optimized)!"
echo "--------------------------------------------------------"
printf "Usage Info:\n"
printf " 1. Select text you want to read.\n"
printf " 2. Press ${B}Super + Shift + O${NC}.\n"
echo ""
