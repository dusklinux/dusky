#!/usr/bin/env bash
#d: Provision the ML training stack (PyTorch + HuggingFace + Unsloth)

# -----------------------------------------------------------------------------
# Script: 400_ml_training_stack.sh
# Purpose: Creates an isolated uv venv at ~/.local/lib/dusky-ml/.venv with
#          vendor-matched PyTorch and the fine-tuning ecosystem:
#
#            PyTorch        NVIDIA -> PyPI wheels (CUDA runtime bundled)
#                           AMD    -> download.pytorch.org/whl/rocm wheels
#                           else   -> PyPI wheels (CPU)
#            HuggingFace    transformers, peft, trl, datasets, accelerate,
#                           huggingface-hub
#            Unsloth        unsloth + unsloth_zoo (CUDA first-class; on AMD
#                           ROCm support is partial -- installed best-effort)
#            NVIDIA extras  bitsandbytes (skipped on AMD: no official build)
#
#          Also installs ~/.local/bin/dusky-ml-doctor
#          (from user_scripts/llm/ml_stack/dusky_ml_doctor.py).
#
# Flags:   --auto           no prompts (still hardware-gated)
#          --rocm-index URL override the PyTorch ROCm wheel index
#                           (default: https://download.pytorch.org/whl/rocm6.3)
#          --cpu            force CPU wheels even when a GPU is present
# -----------------------------------------------------------------------------

set -euo pipefail
shopt -s nullglob

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/gpu_detect.sh
source "${SCRIPT_DIR}/lib/gpu_detect.sh"

AUTO_MODE=0
FORCE_CPU=0
ROCM_INDEX="https://download.pytorch.org/whl/rocm6.3"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --auto)       AUTO_MODE=1 ;;
        --cpu)        FORCE_CPU=1 ;;
        --rocm-index) [[ $# -ge 2 ]] || die "--rocm-index needs a URL"; ROCM_INDEX="$2"; shift ;;
        *) die "Unknown argument '$1' (supported: --auto, --cpu, --rocm-index URL)" 2 ;;
    esac
    shift
done

if [[ $EUID -eq 0 ]]; then
    die "Do not run as root. Run as normal user."
fi

ML_HOME="${HOME}/.local/lib/dusky-ml"
ML_VENV="${ML_HOME}/.venv"
DOCTOR_SRC="${SCRIPT_DIR}/../../llm/ml_stack/dusky_ml_doctor.py"
BIN_DIR="${HOME}/.local/bin"

HF_PACKAGES=(transformers peft trl datasets accelerate huggingface-hub)

# --- main -------------------------------------------------------------------------
main() {
    gpu_detect_topology
    command -v uv &>/dev/null || gpu_pacman_install "uv"
    command -v uv &>/dev/null || die "uv is required (pacman -S uv)."

    local backend="cpu"
    if [[ "$FORCE_CPU" -eq 0 ]]; then
        if [[ "$GPU_HAS_NVIDIA" -eq 1 ]]; then
            backend="cuda"
        elif [[ "$GPU_HAS_AMD" -eq 1 ]] && gpu_rocm_available; then
            backend="rocm"
        elif [[ "$GPU_HAS_AMD" -eq 1 ]]; then
            log_warn "AMD GPU present but no ROCm stack (run 396_amd_rocm_stack.sh); using CPU wheels."
        fi
    fi

    log_info "Provisioning dusky-ml venv at ${ML_VENV} (backend: ${backend})."
    log_info "Wheel downloads are large: ~2.5 GiB CUDA / ~2 GiB ROCm / ~200 MiB CPU."
    gpu_confirm "  Install the ML training stack?" "$AUTO_MODE" || { log_warn "Skipping."; exit 0; }

    mkdir -p "$ML_HOME" "$BIN_DIR"
    if [[ ! -x "${ML_VENV}/bin/python" ]]; then
        uv venv --python-preference only-system "$ML_VENV"
    fi
    local vpy="${ML_VENV}/bin/python"

    # --- PyTorch, vendor-matched ------------------------------------------------
    case "$backend" in
        cuda)
            # PyPI linux wheels bundle the CUDA runtime -- no toolkit needed.
            uv pip install --python "$vpy" torch torchvision torchaudio
            ;;
        rocm)
            # ROCm wheels live on a dedicated index; PyPI torch has no HIP.
            uv pip install --python "$vpy" torch torchvision torchaudio --index-url "$ROCM_INDEX"
            ;;
        *)
            uv pip install --python "$vpy" torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
            ;;
    esac
    log_ok "PyTorch installed (${backend})."

    # --- HuggingFace fine-tuning core --------------------------------------------
    uv pip install --python "$vpy" "${HF_PACKAGES[@]}"
    log_ok "HuggingFace stack installed: ${HF_PACKAGES[*]}"

    # --- Unsloth --------------------------------------------------------------------
    if [[ "$backend" == "cuda" ]]; then
        uv pip install --python "$vpy" unsloth unsloth_zoo
        log_ok "Unsloth installed (CUDA first-class support)."
    elif [[ "$backend" == "rocm" ]]; then
        log_warn "Unsloth on AMD/ROCm is best-effort (upstream targets CUDA)."
        if gpu_confirm "  Attempt Unsloth install anyway?" "$AUTO_MODE"; then
            uv pip install --python "$vpy" unsloth unsloth_zoo
        else
            log_warn "Skipping Unsloth."
        fi
    else
        log_info "Unsloth requires a GPU; skipping on CPU backend."
    fi

    # --- vendor-gated extras ----------------------------------------------------------
    if [[ "$backend" == "cuda" ]]; then
        uv pip install --python "$vpy" bitsandbytes
        log_ok "bitsandbytes installed (NVIDIA)."
    fi

    # --- doctor -----------------------------------------------------------------------
    if [[ -f "$DOCTOR_SRC" ]]; then
        install -D -m 0755 "$DOCTOR_SRC" "${BIN_DIR}/dusky-ml-doctor"
        log_ok "Installed ${BIN_DIR}/dusky-ml-doctor"
    else
        log_warn "dusky_ml_doctor.py source not found; skipping doctor install."
    fi

    echo ""
    log_ok "ML training stack ready."
    echo "   python:      ${vpy}"
    echo "   health check: ${BIN_DIR}/dusky-ml-doctor"
    echo "   quick start:  ${vpy} -c 'import torch; print(torch.__version__, torch.cuda.is_available() or torch.version.hip)'"
}

main "$@"