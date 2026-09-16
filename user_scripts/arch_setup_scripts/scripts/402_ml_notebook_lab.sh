#!/usr/bin/env bash
#d: Install the ML notebook lab (JupyterLab, VS Code, project template)

# -----------------------------------------------------------------------------
# Script: 402_ml_notebook_lab.sh
# Purpose: Everyday ML engineering environment:
#
#            * JupyterLab + ipykernel into the dusky-ml venv
#              (creates the venv CPU-only if 400 has not run yet)
#            * VS Code from [extra] (--code-bin switches to the Microsoft
#              binary build from AUR, with the extension marketplace)
#            * ~/Projects/ml-template/ -- uv-based starter project
#              (pyproject.toml, .gitignore, notebooks/, src/)
#
# Flags:   --auto      no prompts
#          --code-bin  install visual-studio-code-bin (AUR) instead of code
# -----------------------------------------------------------------------------

set -euo pipefail
shopt -s nullglob

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/gpu_detect.sh
source "${SCRIPT_DIR}/lib/gpu_detect.sh"

AUTO_MODE=0
CODE_BIN=0
for arg in "$@"; do
    case "$arg" in
        --auto)    AUTO_MODE=1 ;;
        --code-bin) CODE_BIN=1 ;;
        *) die "Unknown argument '$arg' (supported: --auto, --code-bin)" 2 ;;
    esac
done

if [[ $EUID -eq 0 ]]; then
    die "Do not run as root. Run as normal user."
fi

ML_HOME="${HOME}/.local/lib/dusky-ml"
ML_VENV="${ML_HOME}/.venv"
TEMPLATE_DIR="${HOME}/Projects/ml-template"

# --- Jupyter -------------------------------------------------------------------------
install_jupyter() {
    command -v uv &>/dev/null || gpu_pacman_install "uv"
    if [[ ! -x "${ML_VENV}/bin/python" ]]; then
        log_info "dusky-ml venv missing; creating a CPU one (run 400 for GPU torch)."
        mkdir -p "$ML_HOME"
        uv venv --python-preference only-system "$ML_VENV"
    fi
    uv pip install --python "${ML_VENV}/bin/python" jupyterlab ipykernel
    log_ok "JupyterLab installed in ${ML_VENV}"
    log_info "Launch: ${ML_VENV}/bin/jupyter lab"
}

# --- VS Code ---------------------------------------------------------------------------
install_vscode() {
    if [[ "$CODE_BIN" -eq 1 ]]; then
        gpu_aur_install "visual-studio-code-bin"
    else
        gpu_pacman_install "code"
    fi
    log_ok "VS Code installed."
    log_info "Recommended extensions: ms-python.python, ms-toolsai.jupyter"
}

# --- project template ----------------------------------------------------------------------
install_template() {
    [[ -d "$TEMPLATE_DIR" ]] && { log_ok "Template already exists at ${TEMPLATE_DIR}."; return; }

    mkdir -p "${TEMPLATE_DIR}/notebooks" "${TEMPLATE_DIR}/src"

    cat >"${TEMPLATE_DIR}/pyproject.toml" <<'EOF'
[project]
name = "ml-template"
version = "0.1.0"
description = "Dusky ML starter project"
requires-python = ">=3.11"
dependencies = [
    "torch",
    "transformers",
    "datasets",
    "accelerate",
]

[tool.uv]
# The dusky ML venv already carries vendor-matched torch; `uv venv` here keeps
# experiments isolated instead.
EOF

    cat >"${TEMPLATE_DIR}/.gitignore" <<'EOF'
.venv/
__pycache__/
*.egg-info/
.ipynb_checkpoints/
# models & datasets never belong in git
*.gguf
*.safetensors
*.bin
runs/
wandb/
EOF

    cat >"${TEMPLATE_DIR}/README.md" <<'EOF'
# ml-template

Dusky ML starter. Copy it per experiment: `cp -r ~/Projects/ml-template ~/Projects/my-exp`

- `notebooks/` -- JupyterLab notebooks
- `src/` -- importable package code
- system-wide GPU venv: `~/.local/lib/dusky-ml/.venv` (torch matched to your GPU)
- health check: `dusky-ml-doctor`
EOF

    cat >"${TEMPLATE_DIR}/notebooks/01_start_here.ipynb" <<'EOF'
{
 "cells": [
  {
   "cell_type": "markdown",
   "metadata": {},
   "source": ["# Start here\n", "\n", "Kernel: select the `dusky-ml` venv python (`~/.local/lib/dusky-ml/.venv/bin/python`)."]
  },
  {
   "cell_type": "code",
   "execution_count": null,
   "metadata": {},
   "outputs": [],
   "source": ["import torch\n", "print(torch.__version__)\n", "print('cuda:', torch.cuda.is_available())\n", "print('hip:', torch.version.hip)"]
  }
 ],
 "metadata": {
  "kernelspec": {"display_name": "dusky-ml", "language": "python", "name": "dusky-ml"},
  "language_info": {"name": "python"}
 },
 "nbformat": 4,
 "nbformat_minor": 5
}
EOF

    touch "${TEMPLATE_DIR}/src/__init__.py"
    log_ok "Project template created at ${TEMPLATE_DIR}"
}

# --- main -------------------------------------------------------------------------------------
main() {
    log_info "ML notebook lab: JupyterLab (dusky-ml venv) + VS Code + project template."
    gpu_confirm "  Install the notebook lab?" "$AUTO_MODE" || { log_warn "Skipping."; exit 0; }

    install_jupyter
    if gpu_confirm "  Install VS Code?" "$AUTO_MODE"; then
        install_vscode
    fi
    install_template

    echo ""
    log_ok "Notebook lab ready."
    echo "   Jupyter:     ${ML_VENV}/bin/jupyter lab"
    echo "   Template:    cp -r ${TEMPLATE_DIR} ~/Projects/<name>"
}

main "$@"