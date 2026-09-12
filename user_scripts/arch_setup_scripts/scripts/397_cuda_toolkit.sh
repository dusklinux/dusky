#!/usr/bin/env bash
#d: Install the NVIDIA CUDA toolkit (nvcc, cuDNN) for ML engineering

# -----------------------------------------------------------------------------
# Script: 397_cuda_toolkit.sh
# Purpose: Installs the CUDA toolkit + cuDNN so NVIDIA machines can compile
#          and profile CUDA code (PyTorch/Unsloth/vLLM ship their own CUDA
#          *runtimes*; the toolkit is for development and custom kernels).
#
#          Vendor truth: CUDA is NVIDIA-only. AMD machines are skipped by
#          design (ROCm is handled by 396_amd_rocm_stack.sh).
#
#          * base:     cuda, cudnn
#          * --tools:  nsight-systems, nsight-compute (profiling)
#          * validates driver <-> toolkit compatibility via nvidia-smi
#            (the driver's "CUDA Version" field is the max supported)
#
# Flags:   --auto    no prompts (still hardware-gated)
#          --tools   add profiling/debug tooling (~4 GiB extra)
# -----------------------------------------------------------------------------

set -euo pipefail
shopt -s nullglob

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/gpu_detect.sh
source "${SCRIPT_DIR}/lib/gpu_detect.sh"

AUTO_MODE=0
WITH_TOOLS=0
for arg in "$@"; do
    case "$arg" in
        --auto)  AUTO_MODE=1 ;;
        --tools) WITH_TOOLS=1 ;;
        *) die "Unknown argument '$arg' (supported: --auto, --tools)" 2 ;;
    esac
done

if [[ $EUID -eq 0 ]]; then
    die "Do not run as root. Run as normal user; sudo will be invoked automatically."
fi

CUDA_PKGS=("cuda" "cudnn")
# no cuda-tools on Arch -- nsight-* are the standalone profiling packages.
CUDA_TOOL_PKGS=("nsight-systems" "nsight-compute")

# --- driver <-> toolkit compatibility --------------------------------------------
# nvidia-smi prints "CUDA Version: X.Y" = maximum CUDA the loaded driver can
# run. Warn (never fail) when the toolkit outruns the driver.
check_driver_compat() {
    if ! command -v nvidia-smi &>/dev/null; then
        log_warn "nvidia-smi not found -- proprietary driver not installed/loaded yet."
        log_warn "Run 380_nvidia_open_source.sh and reboot before using the toolkit."
        return
    fi
    local driver_cuda driver_major
    driver_cuda=$(nvidia-smi 2>/dev/null | grep -oE 'CUDA Version: *[0-9]+\.[0-9]+' | grep -oE '[0-9]+\.[0-9]+' | head -n1) || true
    driver_major=$(gpu_nvidia_driver_major) || true
    if [[ -z "$driver_cuda" ]]; then
        log_warn "Could not read the driver's supported CUDA version."
        return
    fi
    log_ok "Driver supports up to CUDA ${driver_cuda} (driver major: ${driver_major:-unknown})."

    local toolkit_cuda
    toolkit_cuda=$(pacman -Qi cuda 2>/dev/null | grep -oE 'cuda-([0-9]+\.[0-9]+)' | head -n1 | cut -d- -f2) || true
    if [[ -n "$toolkit_cuda" ]]; then
        if awk -v t="$toolkit_cuda" -v d="$driver_cuda" 'BEGIN { exit (t > d) ? 0 : 1 }'; then
            log_warn "Toolkit CUDA ${toolkit_cuda} > driver-supported ${driver_cuda}: update nvidia-utils or downgrade cuda."
        else
            log_ok "Toolkit CUDA ${toolkit_cuda} within driver support (${driver_cuda})."
        fi
    fi
}

# --- main --------------------------------------------------------------------------
main() {
    gpu_detect_topology

    if [[ "$GPU_HAS_NVIDIA" -ne 1 ]]; then
        log_info "No NVIDIA GPU detected -- CUDA toolkit not applicable."
        [[ "$GPU_HAS_AMD" -eq 1 ]] && log_info "AMD detected: use 396_amd_rocm_stack.sh for ROCm/HIP."
        exit 0
    fi
    if [[ "$GPU_IS_VM" -eq 1 ]]; then
        log_warn "Virtual GPU detected -- skipping CUDA toolkit."
        exit 0
    fi

    local pkgs=("${CUDA_PKGS[@]}")
    [[ "$WITH_TOOLS" -eq 1 ]] && pkgs+=("${CUDA_TOOL_PKGS[@]}")

    log_info "NVIDIA GPU detected. The CUDA toolkit is a development stack:"
    log_info "PyTorch/Ollama/vLLM bundle their own CUDA runtimes and do NOT need it."
    log_info "Measured on current [extra]: ~2.7 GiB download, ~5.8 GiB installed on disk."
    gpu_confirm "  Install the CUDA toolkit?" "$AUTO_MODE" || { log_warn "Skipping CUDA toolkit."; exit 0; }

    gpu_pacman_install "${pkgs[@]}"
    log_ok "CUDA packages installed."

    # Arch puts nvcc in /usr/bin and libraries in /usr/lib -- no PATH or
    # LD_LIBRARY_PATH changes are required (unlike upstream NVIDIA docs).
    if command -v nvcc &>/dev/null; then
        log_ok "nvcc: $(nvcc --version | tail -n1 | sed 's/^ *//')"
    else
        log_warn "nvcc not on PATH; open a new shell."
    fi

    check_driver_compat

    echo ""
    log_ok "CUDA toolkit ready."
    log_info "Next: 400_ml_training_stack.sh provisions PyTorch + Unsloth (own CUDA runtime)."
}

main "$@"