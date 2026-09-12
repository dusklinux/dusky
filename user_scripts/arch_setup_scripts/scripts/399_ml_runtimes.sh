#!/usr/bin/env bash
#d: Install ML runtimes (Ollama GPU variant, llama.cpp, optional LM Studio)

# -----------------------------------------------------------------------------
# Script: 399_ml_runtimes.sh
# Purpose: Installs the local-LLM runtimes with vendor-matched GPU builds so
#          inference actually lands on the GPU:
#
#            Ollama    NVIDIA -> ollama-cuda     (extra)
#                      AMD    -> ollama-rocm     (extra, needs 396 ROCm stack)
#                      else   -> ollama          (extra, CPU)
#            llama.cpp NVIDIA -> llama-cpp + ggml-cuda   (extra)
#                      AMD    -> llama-cpp + ggml-hip    (extra, needs 396)
#                      else   -> llama-cpp + ggml-vulkan (extra, any GPU with
#                               a Vulkan driver -- including AMD without ROCm)
#            LM Studio opt-in only (--lmstudio): AUR 'lmstudio-bin', CUDA on
#                      NVIDIA, Vulkan on AMD (no ROCm build exists)
#
#          Also installs the llama-server systemd user unit
#          (user_scripts/llm/llama_server/) exposing an OpenAI-compatible
#          API on 127.0.0.1:8081 -- not auto-started.
#
# Flags:   --auto       no prompts (still hardware-gated)
#          --lmstudio   also install LM Studio (AUR, ~500 MiB)
# -----------------------------------------------------------------------------

set -euo pipefail
shopt -s nullglob

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/gpu_detect.sh
source "${SCRIPT_DIR}/lib/gpu_detect.sh"

AUTO_MODE=0
WITH_LMSTUDIO=0
for arg in "$@"; do
    case "$arg" in
        --auto)     AUTO_MODE=1 ;;
        --lmstudio) WITH_LMSTUDIO=1 ;;
        *) die "Unknown argument '$arg' (supported: --auto, --lmstudio)" 2 ;;
    esac
done

if [[ $EUID -eq 0 ]]; then
    die "Do not run as root. Run as normal user; sudo will be invoked automatically."
fi

LLAMA_SERVER_SRC="${SCRIPT_DIR}/../../llm/llama_server"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
LLAMA_SERVER_CONF_DIR="${HOME}/.config/dusky/llama_server"
LLAMA_MODEL_DIR="${HOME}/.local/share/dusky-llama/models"

# --- Ollama -------------------------------------------------------------------------
install_ollama() {
    local variant="ollama"
    if [[ "$GPU_HAS_NVIDIA" -eq 1 ]]; then
        variant="ollama-cuda"
    elif [[ "$GPU_HAS_AMD" -eq 1 ]]; then
        if gpu_rocm_available; then
            variant="ollama-rocm"
        else
            log_warn "AMD GPU present but no ROCm stack (run 396_amd_rocm_stack.sh)."
            log_warn "Installing CPU ollama; re-run after 396 for the ROCm build."
        fi
    fi

    local installed
    installed=$(pacman -Qq 2>/dev/null | grep -E '^ollama(-cuda|-rocm)?$' | head -n1) || true

    if [[ "$installed" == "$variant" ]]; then
        log_ok "Ollama already installed as '$variant'."
    elif [[ -n "$installed" && "$installed" != "ollama" && "$variant" == "ollama" ]]; then
        log_ok "GPU-specific '$installed' installed; keeping it (superset of CPU ollama)."
    elif [[ -n "$installed" ]]; then
        log_info "Replacing '$installed' with '$variant' for GPU acceleration..."
        gpu_pacman_install "$variant"
    else
        log_info "Installing Ollama ('$variant')..."
        gpu_pacman_install "$variant"
    fi

    if systemctl is-active --quiet ollama.service 2>/dev/null; then
        log_ok "ollama.service is running."
    else
        log_info "Starting ollama.service..."
        sudo -n systemctl enable --now ollama.service 2>/dev/null \
            || systemctl --user enable --now ollama.service 2>/dev/null \
            || log_warn "Could not start ollama.service; start it manually."
    fi
}

# --- llama.cpp ------------------------------------------------------------------------
# Arch packages llama.cpp's compute backends as separate ggml packages in
# [extra], so the vendor-matched GPU build is a plain pacman install:
#   ggml-cuda   (NVIDIA, pulls the cuda toolkit)
#   ggml-hip    (AMD, pulls the same ROCm stack 396 installs)
#   ggml-vulkan (anything with a Vulkan driver -- including AMD without ROCm)
install_llama_cpp() {
    local pkgs=("llama-cpp")
    local backend="ggml-vulkan"
    if [[ "$GPU_HAS_NVIDIA" -eq 1 ]]; then
        backend="ggml-cuda"
    elif [[ "$GPU_HAS_AMD" -eq 1 ]] && gpu_rocm_available; then
        backend="ggml-hip"
    fi
    pkgs+=("$backend")
    log_info "Installing llama.cpp with the $backend backend..."
    gpu_pacman_install "${pkgs[@]}"
    if command -v llama-server &>/dev/null; then
        log_ok "llama-server: $(command -v llama-server)"
    else
        log_warn "llama-server binary not found on PATH."
    fi
}

# --- llama-server user unit --------------------------------------------------------------
install_llama_server_unit() {
    [[ -f "${LLAMA_SERVER_SRC}/llama-server.service" ]] || { log_warn "llama-server unit source missing; skipping."; return; }

    mkdir -p "$SYSTEMD_USER_DIR" "$LLAMA_SERVER_CONF_DIR" "$LLAMA_MODEL_DIR"

    cp -f "${LLAMA_SERVER_SRC}/llama-server.service" "${SYSTEMD_USER_DIR}/llama-server.service"
    if [[ ! -f "${LLAMA_SERVER_CONF_DIR}/llama-server.env" ]]; then
        cp -f "${LLAMA_SERVER_SRC}/llama-server.env" "${LLAMA_SERVER_CONF_DIR}/llama-server.env"
    fi
    if command -v systemctl &>/dev/null; then
        # Tolerate environments where no user manager is running (containers,
        # non-lingering ssh sessions): the unit is still installed correctly.
        systemctl --user daemon-reload 2>/dev/null \
            || log_warn "user systemd manager not reachable; unit installed but not reloaded."
    else
        log_warn "systemctl not available; unit installed but not reloaded."
    fi
    log_ok "llama-server user unit installed (NOT started -- see ~/.config/dusky/llama_server/)."
    log_info "Start it with:   systemctl --user start llama-server"
    log_info "Endpoint:        http://127.0.0.1:8081/v1"
}

# --- LM Studio (opt-in) --------------------------------------------------------------------
install_lmstudio() {
    log_info "LM Studio: proprietary desktop app (AUR 'lmstudio-bin', ~500 MiB download)."
    log_info "GPU support inside LM Studio: CUDA on NVIDIA, Vulkan on AMD (no ROCm build exists)."
    gpu_aur_install "lmstudio-bin"
}

# --- main -------------------------------------------------------------------------------------
main() {
    gpu_detect_topology
    log_info "Primary GPU vendor: ${GPU_PRIMARY_VENDOR:-none detected}"

    log_info "Installing Ollama (runtime for the Dusky LLM side panel)..."
    install_ollama

    if gpu_confirm "  Install llama.cpp (vendor-matched build)?" "$AUTO_MODE"; then
        install_llama_cpp
    else
        log_warn "Skipping llama.cpp."
    fi

    install_llama_server_unit

    if [[ "$WITH_LMSTUDIO" -eq 1 ]]; then
        install_lmstudio
    fi

    echo ""
    log_ok "ML runtimes installed."
    log_info "Next (opt-in, in profiles/01_main.toml):"
    echo "   400_ml_training_stack.sh  PyTorch + HuggingFace + Unsloth"
    echo "   401_ml_serving_stack.sh   vLLM serving"
    echo "   402_ml_notebook_lab.sh    JupyterLab + VS Code + project template"
}

main "$@"