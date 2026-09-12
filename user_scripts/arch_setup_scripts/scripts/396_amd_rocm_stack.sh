#!/usr/bin/env bash
#d: Install the AMD ROCm compute stack (ML/HIP acceleration)

# -----------------------------------------------------------------------------
# Script: 396_amd_rocm_stack.sh
# Purpose: Installs the ROCm/HIP userspace so AMD GPUs can accelerate ML
#          workloads (ollama-rocm, llama-cpp with the ggml-hip backend,
#          torch rocm wheels, onnxruntime ROCm EPs used by Kokoro TTS and
#          Parakeet STT).
#
#          Vendor truth: ROCm is AMD-only. NVIDIA machines are skipped by
#          design (CUDA is handled by 397_cuda_toolkit.sh).
#
#          * pacman packages: rocm-hip-runtime, rocblas, hipblas, miopen-hip,
#            rccl, hip-runtime-amd (ships hipcc), rocm-smi-lib, rocminfo
#          * adds the invoking user to render+video (KFD access control)
#          * writes /etc/profile.d/10-rocm.sh with PATH +, when needed,
#            HSA_OVERRIDE_GFX_VERSION for consumer cards without native
#            MIOpen/rocBLAS kernels (same mapping as the Kokoro installer)
#
# Flags:   --auto    no prompts (still hardware-gated)
#          --python  also install python-onnxruntime-rocm from [extra]
# -----------------------------------------------------------------------------

set -euo pipefail
shopt -s nullglob

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/gpu_detect.sh
source "${SCRIPT_DIR}/lib/gpu_detect.sh"

AUTO_MODE=0
WITH_PYTHON=0
for arg in "$@"; do
    case "$arg" in
        --auto)   AUTO_MODE=1 ;;
        --python) WITH_PYTHON=1 ;;
        *) die "Unknown argument '$arg' (supported: --auto, --python)" 2 ;;
    esac
done

if [[ $EUID -eq 0 ]]; then
    die "Do not run as root. Run as normal user; sudo will be invoked automatically."
fi

ROCM_PKGS=(
    "rocm-hip-runtime"   # HIP runtime + device libraries
    "rocblas"            # BLAS on AMD GPUs
    "hipblas"            # HIP BLAS binding layer
    "miopen-hip"         # deep-learning primitives (conv/pool)
    "rccl"               # multi-GPU collectives (NCCL equivalent)
    "hip-runtime-amd"    # ships the hipcc compiler (/usr/bin/hipcc, /opt/rocm/bin/hipcc)
    "rocm-smi-lib"       # rocm-smi monitoring
    "rocminfo"           # topology/gfx enumeration
)

PROFILE_FILE="/etc/profile.d/10-rocm.sh"

# --- groups ---------------------------------------------------------------------
# KFD node access is granted through the render+video groups (see the Arch
# ROCm wiki page). Idempotent; also covered by 473_add_user_to_group.sh.
ensure_groups() {
    local user="${SUDO_USER:-$USER}" group changed=0
    for group in render video; do
        if ! id -nG "$user" 2>/dev/null | tr ' ' '\n' | grep -qx "$group"; then
            # Plain sudo (a prompt is fine); guarded so a failed sudo can never
            # hard-trip set -e mid-install.
            if sudo usermod -aG "$group" "$user"; then
                changed=1
                log_ok "Added $user to group '$group' (effective on next login)."
            else
                log_warn "Could not add $user to '$group'; run: sudo usermod -aG $group $user"
            fi
        fi
    done
    [[ $changed -eq 1 ]] && log_warn "Log out/in once for the new groups to apply."
}

# --- profile.d -------------------------------------------------------------------
write_profile() {
    local hsa_override="$1" content
    content="# Managed by dusky 396_amd_rocm_stack.sh -- safe to edit manually\n"
    content+="# ROCm userspace on Arch keeps binaries in /opt/rocm\n"
    content+="export PATH=\"\$PATH:/opt/rocm/bin\"\n"
    if [[ -n "$hsa_override" ]]; then
        content+="# Consumer gfx target without native MIOpen/rocBLAS kernels; map to closest arch.\n"
        content+="export HSA_OVERRIDE_GFX_VERSION=\"${hsa_override}\"\n"
    fi

    if gpu_root_write "$PROFILE_FILE" "$(printf '%b' "$content")"; then
        log_ok "Wrote ${PROFILE_FILE}"
        [[ -n "$hsa_override" ]] && log_ok "HSA_OVERRIDE_GFX_VERSION=${hsa_override}"
    else
        log_warn "Could not write ${PROFILE_FILE}; set PATH/HSA override manually."
    fi
}

# --- main -------------------------------------------------------------------------
main() {
    gpu_detect_topology

    if [[ "$GPU_HAS_AMD" -ne 1 ]]; then
        log_info "No AMD GPU detected -- ROCm stack not applicable."
        [[ "$GPU_HAS_NVIDIA" -eq 1 ]] && log_info "NVIDIA detected: use 397_cuda_toolkit.sh for CUDA."
        exit 0
    fi
    if [[ "$GPU_IS_VM" -eq 1 ]]; then
        log_warn "Virtual GPU detected -- skipping ROCm."
        exit 0
    fi

    log_info "AMD GPU detected. ROCm provides HIP/ML acceleration (CUDA is NVIDIA-only)."
    log_info "Measured on current [extra]: ~1.1 GiB download, ~25 GiB installed on disk."
    log_info "(AMD ships precompiled kernels for many gfx targets inside every library.)"
    gpu_confirm "  Install the ROCm compute stack?" "$AUTO_MODE" || { log_warn "Skipping ROCm."; exit 0; }

    gpu_pacman_install "${ROCM_PKGS[@]}"
    log_ok "ROCm packages installed."

    [[ "$WITH_PYTHON" -eq 1 ]] && gpu_pacman_install "python-onnxruntime-rocm"

    ensure_groups

    # gfx override for consumer cards (same table as Kokoro TTS)
    local gfx hsa_override=""
    gfx=$(gpu_rocm_gfx) || true
    if [[ -n "$gfx" ]]; then
        hsa_override=$(gpu_hsa_override_for_gfx "$gfx") || true
        if [[ -n "$hsa_override" ]]; then
            log_warn "$gfx has no native MIOpen/rocBLAS kernels; applying HSA_OVERRIDE_GFX_VERSION=$hsa_override."
        else
            log_ok "ROCm target: $gfx (native kernels available)."
        fi
    else
        log_warn "rocminfo reported no gfx target; continuing without HSA override."
    fi
    write_profile "$hsa_override"

    # --- verification ---
    [[ -e /dev/kfd ]] || log_warn "/dev/kfd missing -- amdgpu KFD not loaded; ROCm will not work until reboot."
    if command -v rocm-smi &>/dev/null; then
        if rocm-smi --showproductname &>/dev/null; then
            log_ok "rocm-smi talks to the GPU."
        else
            log_warn "rocm-smi present but failed; check groups/reboot."
        fi
    fi
    local rel
    rel=$(gpu_rocm_release) || true
    [[ -n "$rel" ]] && log_ok "ROCm release: $rel"

    echo ""
    log_ok "ROCm stack ready. Reboot if /dev/kfd or group membership was just added."
    log_info "Per-tool notes:"
    echo "   - Ollama:        399_ml_runtimes.sh installs the ollama-rocm variant"
    echo "   - llama.cpp:     399_ml_runtimes.sh installs llama-cpp with the ggml-hip backend"
    echo "   - Kokoro TTS:    re-run its installer and pick the AMD/ROCm backend"
    echo "   - PyTorch:       400_ml_training_stack.sh provisions torch-rocm wheels"
}

main "$@"