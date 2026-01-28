#!/usr/bin/env bash
# ==============================================================================
# Title:        Parakeet ASR Installer & Setup
# Description:  Automates the setup of NVIDIA Parakeet ASR on Arch Linux/Hyprland.
#               Handles dependencies, venv creation, and initial model caching.
# Author:       Elite DevOps
# Environment:  Arch Linux | Hyprland | UWSM
# ==============================================================================

# --- Strict Error Handling ---
set -euo pipefail

# --- Formatting & Logging ---
# Using ANSI-C quoting ($'...') ensures escape sequences are stored literally
readonly BOLD=$'\033[1m'
readonly RED=$'\033[31m'
readonly GREEN=$'\033[32m'
readonly BLUE=$'\033[34m'
readonly YELLOW=$'\033[33m'
readonly RESET=$'\033[0m'

# Formatting strings separated from data for security
log_info()    { printf "%s[INFO]%s %s\n" "${BLUE}${BOLD}" "${RESET}" "${1:-}"; }
log_success() { printf "%s[OK]%s %s\n" "${GREEN}${BOLD}" "${RESET}" "${1:-}"; }
log_warn()    { printf "%s[WARN]%s %s\n" "${YELLOW}${BOLD}" "${RESET}" "${1:-}"; }
# Errors go to stderr (>2)
log_error()   { printf "%s[ERROR]%s %s\n" "${RED}${BOLD}" "${RESET}" "${1:-}" >&2; exit 1; }

# --- Cleanup Trap ---
cleanup() {
    # Placeholder for future temp file cleanup
    :
}
trap cleanup EXIT

# ==============================================================================
# 1. Privilege & Environment Checks
# ==============================================================================

# Check 1: Ensure NOT running as root
if [[ "${EUID}" -eq 0 ]]; then
    log_error "This script must NOT be run as root. AUR helpers and 'uv' require regular user privileges."
fi

# Check 2: Detect AUR Helper (Prioritize paru, fallback to yay)
AUR_HELPER=""
if command -v paru &>/dev/null; then
    AUR_HELPER="paru"
elif command -v yay &>/dev/null; then
    AUR_HELPER="yay"
else
    log_error "Neither 'paru' nor 'yay' found. Please install an AUR helper first."
fi
readonly AUR_HELPER
log_info "Using AUR helper: ${AUR_HELPER}"

# Check 3: GPU Drivers (CRITICAL)
# The kernel drivers are mandatory. If nvidia-smi fails, no GPU work is possible.
if ! command -v nvidia-smi &>/dev/null; then
    log_error "NVIDIA Drivers (nvidia-smi) not found! You must install 'nvidia-dkms' or 'nvidia' and reboot."
fi

# Check 4: CUDA Toolkit (ADVISORY)
# PyTorch bundles its own runtime, but system CUDA (nvcc) is needed if NeMo compiles extensions.
if ! pacman -Qs cuda >/dev/null && ! command -v nvcc &>/dev/null; then
    log_warn "System-wide CUDA Toolkit (nvcc) not found."
    log_info "PyTorch wheels usually bundle their own runtime, so inference might work without it."
    log_info "However, if NeMo needs to compile custom extensions, this will fail."
    
    printf "%s" "${YELLOW}Do you want to install the system CUDA toolkit to be safe? [y/N] ${RESET}"
    read -r response
    
    if [[ "${response}" =~ ^[yY]([eE][sS])?$ ]]; then
        log_info "Please install your preferred version (e.g., '${AUR_HELPER} -S cuda-12.5 cudnn9.3-cuda12.5') and re-run this script."
        exit 0
    else
        log_info "Proceeding without system CUDA toolkit..."
    fi
else
    log_success "CUDA Toolkit detected (Package or Binary present)."
fi

# Check 5: Check for UV
if ! command -v uv &>/dev/null; then
    log_info "'uv' not found. Installing via ${AUR_HELPER}..."
    "${AUR_HELPER}" -S --needed --noconfirm uv
fi

# ==============================================================================
# 2. System Dependencies
# ==============================================================================

# Check if sentencepiece is already installed to avoid AUR helper prompts
if ! pacman -Qs sentencepiece >/dev/null; then
    log_info "Installing system dependencies (SentencePiece)..."
    "${AUR_HELPER}" -S --needed --noconfirm sentencepiece
else
    log_success "System dependency 'sentencepiece' is already installed."
fi

# ==============================================================================
# 3. Directory & Virtual Environment Setup
# ==============================================================================

readonly BASE_DIR="${HOME}/contained_apps/uv"
readonly VENV_NAME="parakeet"
readonly VENV_DIR="${BASE_DIR}/${VENV_NAME}"

log_info "Setting up directory structure at ${BASE_DIR}..."
mkdir -p "${BASE_DIR}"

# Navigate to base directory
cd "${BASE_DIR}"

# Robust check using absolute path
if [[ -d "${VENV_DIR}" ]]; then
    log_warn "Virtual environment '${VENV_NAME}' already exists."
    log_info "Updating existing environment..."
else
    log_info "Creating Python 3.12 virtual environment..."
    uv venv "${VENV_NAME}" --python 3.12
fi

# Activate environment
# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"

# Verify activation
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
    log_error "Failed to activate virtual environment."
fi
log_success "Virtual environment activated: ${VIRTUAL_ENV}"

# Navigate INTO the environment folder
cd "${VENV_DIR}"

# ==============================================================================
# 4. Python Package Installation
# ==============================================================================

log_info "Installing NeMo Toolkit [ASR] (This may take a while)..."
uv pip install -U "nemo_toolkit[asr]"

log_info "Enforcing compatible Numpy version (1.26.4)..."
uv pip install numpy==1.26.4 --force-reinstall

log_info "Installing Flask..."
uv pip install Flask

# ==============================================================================
# 5. Model Download Script Generation
# ==============================================================================

readonly DOWNLOAD_SCRIPT="modeldownload.py"
log_info "Generating ${DOWNLOAD_SCRIPT}..."

cat << 'EOF' > "${DOWNLOAD_SCRIPT}"
import torch
import nemo.collections.asr as nemo_asr
import gc
import sys

# Flush output immediately for real-time bash logging
sys.stdout.reconfigure(line_buffering=True)

print("----------------------------------------------------------------")
print("   Starting Parakeet Model Download & Optimization Protocol")
print("----------------------------------------------------------------")

# 1. Force the download and initial load to happen on the CPU (System RAM)
print("⏳ Loading model to CPU (bypassing VRAM limits)...")
try:
    asr_model = nemo_asr.models.ASRModel.from_pretrained(
        model_name="nvidia/parakeet-tdt-0.6b-v2",
        map_location=torch.device("cpu")
    )
except Exception as e:
    print(f"❌ Error loading model: {e}")
    sys.exit(1)

# 2. Switch to Half Precision (FP16)
print("📉 Converting to Half Precision (FP16) to save VRAM...")
asr_model = asr_model.half()

# 3. Clean up system memory before the move
print("🧹 Cleaning up system memory...")
gc.collect()
torch.cuda.empty_cache()

# 4. Move the streamlined model to the GPU
print("🚀 Moving model to GPU...")
try:
    asr_model = asr_model.cuda()
    print("✅ Success! Model is on GPU and ready for inference.")
except torch.cuda.OutOfMemoryError:
    print("❌ Out of Memory Error.")
    print("   Please close other GPU-heavy apps (browsers, games) and try again.")
    sys.exit(1)
except Exception as e:
    print(f"❌ Unexpected error moving to GPU: {e}")
    sys.exit(1)

print("----------------------------------------------------------------")
print("   Setup Complete.")
print("----------------------------------------------------------------")
EOF

# ==============================================================================
# 6. Execution & Finalization
# ==============================================================================

log_info "Running model download script..."
# Using the python from the active venv
python "${DOWNLOAD_SCRIPT}"

log_success "Installation and Setup Finished Successfully."
log_info "Your environment is located at: ${VENV_DIR}"
