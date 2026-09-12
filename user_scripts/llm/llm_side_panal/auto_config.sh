#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Script: auto_config.sh
# Purpose: Auto-configures everything for Dusky LLM Side Panel:
#          1. Installs & enables systemd user service (dusky_llm.service).
#          2. Registers D-Bus service (org.dusky.llm).
#          3. Checks Ollama service state safely.
#          4. Auto-registers local GGUF model (/mnt/zram1/owao/) into Ollama.
#          5. Adds Hyprland window rules to ~/.config/hypr/source/window_rules.lua.
# -----------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
DBUS_SERVICE_DIR="${HOME}/.local/share/dbus-1/services"
HYPR_RULES_FILE="${HOME}/.config/hypr/source/window_rules.lua"

log_info() { printf '\e[34m[AUTO-CONFIG]\e[0m %s\n' "$*"; }
log_ok()   { printf '\e[32m[OK]\e[0m %s\n' "$*"; }
log_warn() { printf '\e[33m[WARN]\e[0m %s\n' "$*" >&2; }
log_err()  { printf '\e[31m[ERR]\e[0m %s\n' "$*" >&2; }

# 1. Install Systemd Service & DBus Service
mkdir -p "$SYSTEMD_USER_DIR" "$DBUS_SERVICE_DIR"

log_info "Installing systemd user service..."
cp -f "${SCRIPT_DIR}/service/dusky_llm.service" "${SYSTEMD_USER_DIR}/dusky_llm.service"

log_info "Installing D-Bus service..."
cp -f "${SCRIPT_DIR}/service/org.dusky.llm.service" "${DBUS_SERVICE_DIR}/org.dusky.llm.service"

systemctl --user daemon-reload
systemctl --user enable dusky_llm.service
log_ok "Systemd user service enabled."

# 2. Check Dependencies & Ollama
if ! command -v ollama &>/dev/null; then
    # Vendor-matched variant so inference lands on the GPU
    # (installed properly by 399_ml_runtimes.sh; this is the fallback hint).
    OLLAMA_PKG="ollama"
    if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
        OLLAMA_PKG="ollama-cuda"
    elif [[ -e /dev/kfd ]]; then
        OLLAMA_PKG="ollama-rocm"
    fi
    log_warn "Ollama binary is not installed yet."
    log_warn "GPU-matched install: sudo pacman -S ${OLLAMA_PKG}   (or run 399_ml_runtimes.sh)"
else
    log_info "Ollama binary detected."
    if ! systemctl is-active --quiet ollama.service 2>/dev/null; then
        log_info "Attempting non-interactive start of ollama.service..."
        sudo -n systemctl start ollama.service 2>/dev/null || systemctl --user start ollama.service 2>/dev/null || true
    fi
fi

# 3. Auto-import GGUF model from /mnt/zram1/owao/
log_info "Checking GGUF models in /mnt/zram1/owao/..."
python3 -c "
import sys
sys.path.insert(0, '${SCRIPT_DIR}')
from llm_backend import auto_import_local_gguf, get_installed_models
imported = auto_import_local_gguf()
models = get_installed_models()
if imported:
    print(f'GGUF Auto-Import: Registered {imported}')
print(f'Active Ollama models: {models}')
" || log_warn "GGUF check complete."

# 4. Configure Hyprland Window Rule
if [[ -f "$HYPR_RULES_FILE" ]]; then
    if ! grep -q "dusky_llm_side_panal" "$HYPR_RULES_FILE"; then
        log_info "Appending Hyprland window rule to ${HYPR_RULES_FILE}..."
        cat << 'EOF' >> "$HYPR_RULES_FILE"

--- Dusky LLM Side Panel Script ---
hl.window_rule({
    name = "dusky_llm_side_panal",
    match = {
        class = "^(dusky_llm_side_panal\\.py)$",
    },
    float = true,
    animation = "slide right",
    no_dim = true,
    rounding = 16,
    border_size = 0,
    workspace = "unset",
    focus_on_activate = true
})
EOF
        log_ok "Added window rule for dusky_llm_side_panal.py"
    fi
fi

# 5. Start Service & Verify
log_info "Starting dusky_llm.service..."
systemctl --user restart dusky_llm.service
sleep 0.3

if systemctl --user is-active --quiet dusky_llm.service; then
    log_ok "dusky_llm.service is ACTIVE and running!"
else
    log_warn "dusky_llm.service status check complete."
fi

log_ok "Auto-configuration finished!"
