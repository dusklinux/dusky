#!/usr/bin/env bash
#d: Install the ML serving stack (vLLM) with a systemd user unit

# -----------------------------------------------------------------------------
# Script: 401_ml_serving_stack.sh
# Purpose: Provisions vLLM in its own venv (~/.local/lib/dusky-ml/.venv-serving,
#          separate from the training venv: vLLM pins conflict with Unsloth's)
#          and installs a dusky-vllm systemd user unit (not auto-started).
#
#            NVIDIA -> vllm (PyPI, first-class CUDA support)
#            AMD    -> vllm-rocm (PyPI, published by AMD; best-effort)
#            else   -> skipped (use llama-server from 399_ml_runtimes.sh)
#
# Flags:   --auto      no prompts (still hardware-gated)
#          --no-vllm   only install the user unit, skip the vLLM venv
# -----------------------------------------------------------------------------

set -euo pipefail
shopt -s nullglob

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/gpu_detect.sh
source "${SCRIPT_DIR}/lib/gpu_detect.sh"

AUTO_MODE=0
NO_VLLM=0
for arg in "$@"; do
    case "$arg" in
        --auto)   AUTO_MODE=1 ;;
        --no-vllm) NO_VLLM=1 ;;
        *) die "Unknown argument '$arg' (supported: --auto, --no-vllm)" 2 ;;
    esac
done

if [[ $EUID -eq 0 ]]; then
    die "Do not run as root. Run as normal user."
fi

ML_HOME="${HOME}/.local/lib/dusky-ml"
SERVE_VENV="${ML_HOME}/.venv-serving"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
VLLM_CONF_DIR="${HOME}/.config/dusky/vllm"
VLLM_UNIT="${SYSTEMD_USER_DIR}/dusky-vllm.service"

# --- vLLM venv -------------------------------------------------------------------
install_vllm() {
    local pkg="vllm"
    if [[ "$GPU_HAS_AMD" -eq 1 && "$GPU_HAS_NVIDIA" -ne 1 ]]; then
        pkg="vllm-rocm"
        log_warn "vllm-rocm is AMD's build; quality tracks AMD's release cadence, not PyPI vllm."
    fi

    mkdir -p "$ML_HOME"
    if [[ ! -x "${SERVE_VENV}/bin/python" ]]; then
        uv venv --python-preference only-system "$SERVE_VENV"
    fi
    uv pip install --python "${SERVE_VENV}/bin/python" "$pkg"
    log_ok "Installed ${pkg} into ${SERVE_VENV}"
}

# --- user unit ----------------------------------------------------------------------
install_unit() {
    mkdir -p "$SYSTEMD_USER_DIR" "$VLLM_CONF_DIR"

    if [[ ! -f "${VLLM_CONF_DIR}/dusky-vllm.env" ]]; then
        cat >"${VLLM_CONF_DIR}/dusky-vllm.env" <<'EOF'
# dusky-vllm options -- installed by 401_ml_serving_stack.sh, edit freely.
# VLLM_SERVE_ARGS is passed verbatim to `vllm serve`.
# Example:
#   VLLM_SERVE_ARGS=Qwen/Qwen2.5-1.5B-Instruct --port 8000
# Set your token once (read by many HF tools):
#   HF_TOKEN=hf_...
VLLM_SERVE_ARGS=
HF_TOKEN=
EOF
        log_ok "Wrote ${VLLM_CONF_DIR}/dusky-vllm.env"
    fi

    cat >"$VLLM_UNIT" <<EOF
# ~/.config/systemd/user/dusky-vllm.service
# Installed by 401_ml_serving_stack.sh; NOT auto-started (a loaded model
# pins VRAM). Start manually:
#   systemctl --user start dusky-vllm
# OpenAI-compatible endpoint is whatever --port says (default 8000).
[Unit]
Description=Dusky vLLM OpenAI-compatible server
Documentation=https://docs.vllm.ai
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Type=simple
ExecStart=/bin/sh -c 'exec ${SERVE_VENV}/bin/vllm serve \$VLLM_SERVE_ARGS'
EnvironmentFile=%h/.config/dusky/vllm/dusky-vllm.env
Restart=on-failure
RestartSec=3
# GPU access (/dev/nvidia*, /dev/kfd, /dev/dri) lives in the user session.
NoNewPrivileges=yes

[Install]
WantedBy=default.target
EOF
    if command -v systemctl &>/dev/null; then
        systemctl --user daemon-reload
    else
        log_warn "systemctl not available; unit installed but not reloaded."
    fi
    log_ok "Installed ${VLLM_UNIT} (NOT started)."
    log_info "Configure: ${VLLM_CONF_DIR}/dusky-vllm.env"
    log_info "Start:     systemctl --user start dusky-vllm"
}

# --- main ------------------------------------------------------------------------------
main() {
    gpu_detect_topology

    if [[ "$GPU_HAS_NVIDIA" -ne 1 && "$GPU_HAS_AMD" -ne 1 ]]; then
        log_info "No NVIDIA/AMD GPU detected -- vLLM is GPU-only; skipping."
        log_info "For CPU serving use llama-server (399_ml_runtimes.sh)."
        exit 0
    fi

    command -v uv &>/dev/null || gpu_pacman_install "uv"
    command -v uv &>/dev/null || die "uv is required (pacman -S uv)."

    log_info "Serving stack: vLLM (own venv, isolated from training pins) + user unit."
    gpu_confirm "  Install the serving stack?" "$AUTO_MODE" || { log_warn "Skipping."; exit 0; }

    [[ "$NO_VLLM" -eq 0 ]] && install_vllm
    install_unit

    echo ""
    log_ok "Serving stack ready."
    log_info "Health check everything with: dusky-ml-doctor"
}

main "$@"