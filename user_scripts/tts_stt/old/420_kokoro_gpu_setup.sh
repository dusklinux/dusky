#!/usr/bin/env bash
# KOKORO gpu setup
# ==============================================================================
# Script Name: install_kokoro_gpu.sh
# Description: Automates the setup of Kokoro GPU (CUDA/cuDNN) + UV environment.
#              Optimized for Arch Linux/Hyprland/UWSM.
# Author:      DevOps/Arch Architect
# ==============================================================================

# --- Strict Error Handling ---
set -euo pipefail
IFS=$'\n\t'

# --- TTY Detection & Color Definitions ---
# Only use colors if connected to a terminal, otherwise clean output.
if [[ -t 1 ]]; then
    readonly C_RESET=$'\033[0m'
    readonly C_BOLD=$'\033[1m'
    readonly C_GREEN=$'\033[32m'
    readonly C_BLUE=$'\033[34m'
    readonly C_RED=$'\033[31m'
    readonly C_CYAN=$'\033[36m'
else
    readonly C_RESET='' C_BOLD='' C_GREEN='' C_BLUE='' C_RED='' C_CYAN=''
fi

# --- Logging Functions (SC2059 Compliant) ---
log_info()    { printf '%b[INFO]%b %s\n' "$C_BLUE" "$C_RESET" "${1:-}"; }
log_success() { printf '%b[OK]%b   %s\n' "$C_GREEN" "$C_RESET" "${1:-}"; }
log_error()   { printf '%b[ERR]%b  %s\n' "$C_RED" "$C_RESET" "${1:-}" >&2; }
log_step()    { printf '\n%b%b:: %s%b\n' "$C_BOLD" "$C_CYAN" "${1:-}" "$C_RESET"; }

# --- Cleanup Trap ---
cleanup() {
    local exit_code=$?
    if (( exit_code != 0 )); then
        log_error "Script failed with exit code $exit_code."
    fi
}
trap cleanup EXIT

# --- Configuration ---
readonly TARGET_DIR="$HOME/contained_apps/uv/kokoro_gpu"
readonly MODEL_URL_ONNX="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.fp16-gpu.onnx"
readonly MODEL_URL_BIN="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

# ==============================================================================
# 1. Pre-flight Checks
# ==============================================================================

# Ensure NOT running as root
if (( EUID == 0 )); then
    log_error "This script must NOT be run as root."
    log_error "It manages user-local files and runs your AUR helper. Sudo will be requested where necessary."
    exit 1
fi

# Ensure required standard tools exist
for tool in uv curl sudo; do
    if ! command -v "$tool" &>/dev/null; then
        log_error "Required tool '$tool' is not installed."
        exit 1
    fi
done

# Detect AUR Helper (Paru > Yay)
AUR_HELPER=""
if command -v paru &>/dev/null; then
    AUR_HELPER="paru"
elif command -v yay &>/dev/null; then
    AUR_HELPER="yay"
else
    log_error "Neither 'paru' nor 'yay' found."
    log_error "Please install an AUR helper (paru or yay) to continue."
    exit 1
fi
readonly AUR_HELPER

log_info "Environment Check: Arch Linux / Hyprland / UWSM"
log_info "AUR Helper Detected: $AUR_HELPER"
log_info "Target Directory: $TARGET_DIR"

# ==============================================================================
# 2. System Dependencies (Pacman)
# ==============================================================================
log_step "Installing System Dependencies (Pacman)"

if sudo pacman -S --needed --noconfirm mpv ffmpeg wl-clipboard mbuffer; then
    log_success "System dependencies installed."
else
    log_error "Failed to install system dependencies."
    exit 1
fi

# ==============================================================================
# 3. AUR Dependencies (Paru/Yay)
# ==============================================================================
log_step "Installing CUDA/cuDNN Dependencies (AUR)"

log_info "Installing cuda-12.5 and cudnn9.3-cuda12.5 via $AUR_HELPER..."
if "$AUR_HELPER" -S --needed --noconfirm cuda-12.5 cudnn9.3-cuda12.5; then
    log_success "CUDA libraries installed."
else
    log_error "Failed to install AUR dependencies. Check internet connection or AUR status."
    exit 1
fi

# ==============================================================================
# 4. Project Directory Setup
# ==============================================================================
log_step "Setting up Project Directory"

if [[ ! -d "$TARGET_DIR" ]]; then
    mkdir -p "$TARGET_DIR"
    log_success "Created directory: $TARGET_DIR"
else
    log_info "Directory already exists: $TARGET_DIR"
fi

cd "$TARGET_DIR" || { log_error "Failed to enter directory $TARGET_DIR"; exit 1; }

# ==============================================================================
# 5. Python/UV Environment Setup
# ==============================================================================
log_step "Configuring UV Python Environment"

# Initialize UV
if [[ ! -f "pyproject.toml" ]]; then
    log_info "Initializing UV project (Python 3.12)..."
    if ! uv init -p 3.12; then
        log_error "Failed to initialize UV project."
        exit 1
    fi
    log_success "UV initialized."
else
    log_info "UV project already initialized (pyproject.toml found)."
fi

# Add core dependencies
log_info "Adding core dependencies (kokoro-onnx, soundfile)..."
if ! uv add kokoro-onnx soundfile; then
    log_error "Failed to add core dependencies."
    exit 1
fi

# Install GPU specifics
log_info "Installing GPU-specific packages (onnxruntime-gpu, sounddevice)..."
if ! uv pip install onnxruntime-gpu sounddevice; then
    log_error "Failed to install GPU-specific packages."
    exit 1
fi

# Setup internal Pip/Setuptools (User Requirement)
# Note: While UV manages packages, legacy scripts or specific tools may rely
# on the presence of 'pip' or 'setuptools' binaries within the venv.
log_info "Ensuring pip/wheel setup via run (Compatibility Mode)..."
if ! uv run python -m ensurepip --upgrade; then
    log_error "Failed to run ensurepip."
    exit 1
fi

if ! uv run python -m pip install --upgrade pip setuptools wheel; then
    log_error "Failed to upgrade internal pip/setuptools."
    exit 1
fi

log_success "Python environment configured."

# ==============================================================================
# 6. Model Downloads
# ==============================================================================
log_step "Downloading Model Files"

download_if_missing() {
    local url="${1:?URL required}"
    local file="${2:?Filename required}"

    if [[ -f "$file" ]]; then
        log_info "File exists, skipping download: $file"
        return 0
    fi

    log_info "Downloading $file..."
    
    # curl args:
    # -L: Follow redirects
    # -f: Fail fast on HTTP errors (404/500)
    # -#: Progress bar
    # --retry 3: Retry 3 times
    # --retry-delay 2: Wait 2s between retries
    if curl --retry 3 --retry-delay 2 -L -f -# -o "$file" "$url"; then
        log_success "Downloaded $file"
    else
        log_error "Failed to download: $file"
        rm -f "$file"  # Clean up partial/corrupt file
        return 1
    fi
}

download_if_missing "$MODEL_URL_ONNX" "kokoro-v1.0.fp16-gpu.onnx"
download_if_missing "$MODEL_URL_BIN"  "voices-v1.0.bin"

# ==============================================================================
# 7. Completion
# ==============================================================================
printf '\n'
log_success "Kokoro GPU installation complete!"
log_info "Location: $TARGET_DIR"
log_info "Environment is ready for your Python keybind script."
