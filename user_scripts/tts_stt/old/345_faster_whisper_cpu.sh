#!/usr/bin/env bash
# Faster whisper speech to text setup
# ==============================================================================
# Script Name: install_faster_whisper.sh
# Description: Automates the setup of faster-whisper in a strict uv environment.
#              Optimized for Arch Linux/Hyprland (CPU inference).
# Version:     3.0 (Final Polish: Fixed escape codes & Updated Instructions)
# ==============================================================================

# 1. Strict Error Handling
set -euo pipefail

# 2. Configuration & Variables
BASE_DIR="${HOME}/contained_apps/uv"
ENV_NAME="fasterwhisper_cpu"
ENV_PATH="${BASE_DIR}/${ENV_NAME}"
PRELOAD_SCRIPT="${ENV_PATH}/preload_model.py"

# 3. Cleanup & Signal Handling
cleanup() {
    if [[ -f "${PRELOAD_SCRIPT}" ]]; then
        rm -f "${PRELOAD_SCRIPT}"
    fi
}
trap cleanup EXIT

# 4. Formatting Helpers
# We use $'' syntax to force Bash to interpret the escape codes immediately.
BOLD=$'\e[1m'
GREEN=$'\e[32m'
BLUE=$'\e[34m'
YELLOW=$'\e[33m'
RED=$'\e[31m'
RESET=$'\e[0m'

log_info()    { printf "${BLUE}[INFO]${RESET} %s\n" "$1"; }
log_success() { printf "${GREEN}[OK]${RESET} %s\n" "$1"; }
log_warn()    { printf "${YELLOW}[WARN]${RESET} %s\n" "$1"; }
log_error()   { printf "${RED}[ERROR]${RESET} %s\n" "$1"; exit 1; }

# 5. Root/User Check
if [[ "${EUID}" -eq 0 ]]; then
    log_error "This script manages user-space environments in \$HOME. Do NOT run as root/sudo."
fi

# 6. Dependency Check
if ! command -v uv &>/dev/null; then
    log_error "The 'uv' package manager is not installed. Please install it (pacman -S uv)."
fi

if ! command -v python3 &>/dev/null; then
    log_error "Python 3 is not found."
fi

# ==============================================================================
# Interactive Prompt
# ==============================================================================

clear
printf "${BOLD}Faster-Whisper (CPU) Installation Wizard${RESET}\n"
# Safe print for hyphens
printf "%s\n" "--------------------------------------------"
printf "You are about to install the ${BOLD}faster-whisper${RESET} model.\n\n"
printf "This setup uses the ${YELLOW}distil-small.en${RESET} model which is:\n"
printf "  * ${GREEN}CPU Optimized:${RESET} Runs fast without a dedicated GPU.\n"
printf "  * ${GREEN}Space Efficient:${RESET} Requires ~320MB (vs ~8GB for NVIDIA/CUDA).\n\n"

printf "Do you want to proceed with the installation? [y/N] "
read -r response

if [[ ! "$response" =~ ^[Yy]$ ]]; then
    log_info "Installation cancelled by user."
    exit 0
fi

# ==============================================================================
# Installation Logic
# ==============================================================================

# Note: We bold the variable expansion, which now works correctly due to $'' syntax
log_info "Preparing workspace at ${BOLD}${BASE_DIR}${RESET}..."
mkdir -p "$BASE_DIR"
cd "$BASE_DIR"

# Handle existing environment
if [[ -d "$ENV_NAME" ]]; then
    log_warn "Environment directory '${ENV_NAME}' already exists."
    printf "Recreate it? (This will delete the existing env) [y/N] "
    read -r recreate
    if [[ "$recreate" =~ ^[Yy]$ ]]; then
        rm -rf "$ENV_NAME"
        log_info "Removed old environment."
    else
        log_info "Using existing environment. Skipping creation."
    fi
fi

# Create Venv
if [[ ! -d "$ENV_NAME" ]]; then
    log_info "Creating isolated Python environment with uv..."
    uv venv "$ENV_NAME"
    log_success "Virtual environment created."
fi

# Activate
log_info "Activating environment..."
source "${ENV_NAME}/bin/activate"

# Verify activation
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
    log_error "Failed to activate virtual environment."
fi

# Install Package
log_info "Installing faster-whisper via uv pip..."
uv pip install faster-whisper

# ==============================================================================
# Model Auto-Download
# ==============================================================================

log_info "Triggering model download (distil-small.en)..."

# Generate temporary python script
cat <<EOF > "${PRELOAD_SCRIPT}"
from faster_whisper import WhisperModel
import os

print("   -> Initializing model download to ~/.cache/huggingface/hub...")
# This forces the download immediately
model = WhisperModel("distil-small.en", device="cpu", compute_type="int8")
print("   -> Download complete.")
EOF

# Execution
python3 "${PRELOAD_SCRIPT}"

log_success "Installation and Model setup complete!"
printf "\nUse it with the keybind ${BOLD}SUPER + SHIFT + I${RESET}.\n"
