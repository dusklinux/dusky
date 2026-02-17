#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# DUSKY KOKORO INSTALLER V32 (Universal: NVIDIA / AMD / CPU)
# ==============================================================================

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly ENV_DIR="$HOME/contained_apps/uv/dusky_kokoro"
readonly MODEL_DIR="$ENV_DIR/models"
readonly TRIGGER_DIR="$HOME/user_scripts/tts_stt/dusky_kokoro"
readonly TARGET_TRIGGER="$TRIGGER_DIR/trigger.sh"

readonly MODEL_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files/kokoro-v0_19.onnx"
readonly VOICES_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files/voices.bin"

echo ":: [V32] Initializing Dusky Kokoro Universal Setup..."

# --- 1. Hardware Detection Report (Informational Only) ---
echo "--------------------------------------------------------"
echo ":: Hardware Scan:"
GPU_FOUND=false

if command -v lspci &>/dev/null; then
    if lspci | grep -i "nvidia" &>/dev/null; then
        echo "   [✓] NVIDIA GPU Detected"
        GPU_FOUND=true
    fi
    if lspci | grep -i "amd" &>/dev/null || lspci | grep -i "radeon" &>/dev/null; then
        echo "   [✓] AMD GPU Detected"
        GPU_FOUND=true
    fi
else
    echo "   [?] 'lspci' not found. Cannot auto-scan hardware."
fi

if [ "$GPU_FOUND" = false ]; then
    echo "   [!] No dedicated GPU detected (or unknown vendor)."
fi
echo "--------------------------------------------------------"

# --- 2. User Selection (Explicit Intent) ---
echo "Select your installation target:"
echo "  1) NVIDIA (CUDA) - Best for GeForce/RTX cards"
echo "  2) AMD (ROCm)    - Best for Radeon/Instinct cards (Linux only)"
echo "  3) CPU Only      - Works everywhere, no GPU required (Lightweight)"
echo ""
read -p "Enter choice [1-3]: " HW_CHOICE

MODE="cpu"
if [[ "$HW_CHOICE" == "1" ]]; then
    MODE="nvidia"
elif [[ "$HW_CHOICE" == "2" ]]; then
    MODE="amd"
elif [[ "$HW_CHOICE" == "3" ]]; then
    MODE="cpu"
else
    echo ":: Invalid choice. Defaulting to CPU mode."
    MODE="cpu"
fi

echo ":: Selected Mode: ${MODE^^}"

# --- 3. Environment Setup ---
mkdir -p "$ENV_DIR" "$MODEL_DIR" "$TRIGGER_DIR"

if ! command -v uv &> /dev/null; then
    echo ":: Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source "$HOME/.cargo/env"
fi

if [[ -f "$SCRIPT_DIR/dusky_main.py" ]]; then
    cp "$SCRIPT_DIR/dusky_main.py" "$ENV_DIR/"
    echo ":: dusky_main.py deployed."
else
    echo ":: ERROR: dusky_main.py not found in current directory."
    exit 1
fi

cd "$ENV_DIR"
echo ":: Configuring Python Environment..."
uv init --python 3.12 --no-workspace 2>/dev/null || true

# --- 4. Conditional Dependency Installation ---
echo ":: Installing Dependencies for $MODE..."

# Common base deps
uv add "soundfile" "numpy"

case "$MODE" in
    nvidia)
        # Note: kokoro-onnx[gpu] pulls onnxruntime-gpu
        uv add "kokoro-onnx[gpu]" \
               "nvidia-cuda-runtime-cu12" \
               "nvidia-cublas-cu12" \
               "nvidia-cudnn-cu12" \
               "nvidia-cufft-cu12"
        ;;
    amd)
        # Note: Install base kokoro-onnx (no [gpu] extra) to avoid pulling onnxruntime-gpu
        # Then explicitly add onnxruntime-rocm
        uv add "kokoro-onnx" "onnxruntime-rocm"
        ;;
    cpu)
        # Standard CPU runtime
        uv add "kokoro-onnx" "onnxruntime"
        ;;
esac

# --- 5. Model Downloads ---
if [[ ! -f "$MODEL_DIR/kokoro-v0_19.onnx" ]]; then
    echo ":: Downloading ONNX Model..."
    curl -L "$MODEL_URL" -o "$MODEL_DIR/kokoro-v0_19.onnx"
fi
if [[ ! -f "$MODEL_DIR/voices.bin" ]]; then
    echo ":: Downloading Voices..."
    curl -L "$VOICES_URL" -o "$MODEL_DIR/voices.bin"
fi

# --- 6. Generate Trigger (Mode-Aware) ---
echo ":: Generating Trigger Script..."

# We inject the MODE variable into the generated script so it knows if it needs library hacks
cat << EOF > "$TARGET_TRIGGER"
#!/usr/bin/env bash
# Dusky Kokoro Trigger V32 ($MODE edition)

readonly APP_DIR="$HOME/contained_apps/uv/dusky_kokoro"
readonly PID_FILE="/tmp/dusky_kokoro.pid"
readonly READY_FILE="/tmp/dusky_kokoro.ready"
readonly FIFO_PATH="/tmp/dusky_kokoro.fifo"
readonly DAEMON_LOG="/tmp/dusky_kokoro.log"
readonly DEBUG_LOG="\$APP_DIR/dusky_debug.log"
readonly INSTALL_MODE="$MODE"

# --- Helpers ---

get_libs() {
    # NVIDIA-specific library discovery for UV pip packages
    if [[ "\$INSTALL_MODE" == "nvidia" ]]; then
        local SITE_PACKAGES
        SITE_PACKAGES=\$(find "\$APP_DIR/.venv" -type d -name "site-packages" 2>/dev/null | head -n 1)
        if [[ -n "\$SITE_PACKAGES" && -d "\$SITE_PACKAGES/nvidia" ]]; then
            local libs
            libs=\$(find "\$SITE_PACKAGES/nvidia" -type d -name "lib" | tr '\n' ':')
            echo "\${libs%:}"
        fi
    fi
}

notify() { notify-send "\$@" 2>/dev/null || true; }

is_running() {
    [[ -f "\$PID_FILE" ]] && kill -0 "\$(cat "\$PID_FILE" 2>/dev/null)" 2>/dev/null
}

stop_daemon() {
    if [[ -f "\$PID_FILE" ]]; then
        local pid
        pid=\$(cat "\$PID_FILE" 2>/dev/null)
        if [[ -n "\$pid" ]]; then
            kill "\$pid" 2>/dev/null || true
            for _ in {1..30}; do
                kill -0 "\$pid" 2>/dev/null || break
                sleep 0.1
            done
            if kill -0 "\$pid" 2>/dev/null; then
                kill -9 "\$pid" 2>/dev/null || true
            fi
        fi
    fi
    rm -f "\$PID_FILE" "\$FIFO_PATH" "\$READY_FILE"
}

start_daemon() {
    local debug_mode="\${1:-false}"

    if ! command -v mpv &>/dev/null; then
        notify "Kokoro Error" "MPV is missing!"
        return 1
    fi

    # Only set LD_LIBRARY_PATH if we are in NVIDIA mode
    local EXTRA_LIBS
    EXTRA_LIBS=\$(get_libs)
    if [[ -n "\$EXTRA_LIBS" ]]; then
        export LD_LIBRARY_PATH="\${EXTRA_LIBS}\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
    fi

    cd "\$APP_DIR"

    if [[ "\$debug_mode" == "true" ]]; then
        echo ":: Starting Daemon in FORENSIC DEBUG Mode..."
        export DUSKY_LOG_LEVEL="DEBUG"
        export DUSKY_LOG_FILE="\$DEBUG_LOG"
        nohup uv run dusky_main.py --daemon --debug-file "\$DEBUG_LOG" > "\$DAEMON_LOG" 2>&1 &
    else
        nohup uv run dusky_main.py --daemon > "\$DAEMON_LOG" 2>&1 &
    fi

    local daemon_pid=\$!
    echo "\$daemon_pid" > "\$PID_FILE"

    # Wait for daemon ready (30s timeout covers cold boot)
    for _ in {1..300}; do
        if [[ -f "\$READY_FILE" ]]; then
            if [[ "\$debug_mode" == "true" ]]; then
                echo ":: Daemon Ready. Tailing log..."
                tail -f "\$DEBUG_LOG"
            fi
            return 0
        fi
        if ! kill -0 "\$daemon_pid" 2>/dev/null; then
            echo ":: ERROR: Daemon crashed on startup."
            notify "Kokoro Failed" "Daemon crashed."
            return 1
        fi
        sleep 0.1
    done

    echo ":: ERROR: Daemon start timeout (30s)."
    notify "Kokoro Failed" "Timeout."
    return 1
}

# --- CLI Logic ---
case "\${1:-}" in
    --help|-h)
        echo "Usage: trigger.sh [--kill|--restart|--status|--debug|--logs]"
        exit 0
        ;;
    --kill)
        stop_daemon
        echo ":: Stopped."
        exit 0
        ;;
    --status)
        if is_running; then echo ":: Running (PID: \$(cat "\$PID_FILE"))"; else echo ":: Stopped"; fi
        exit 0
        ;;
    --restart)
        stop_daemon
        start_daemon "false"
        exit 0
        ;;
    --logs)
        [[ -f "\$DAEMON_LOG" ]] && tail -f "\$DAEMON_LOG"
        exit 0
        ;;
    --debug)
        stop_daemon
        start_daemon "true"
        exit \$?
        ;;
esac

# --- Trigger Logic ---
if ! is_running; then
    rm -f "\$FIFO_PATH" "\$PID_FILE" "\$READY_FILE"
    if ! start_daemon "false"; then exit 1; fi
fi

# Secondary readiness check
if [[ ! -f "\$READY_FILE" ]]; then
    for _ in {1..300}; do
        [[ -f "\$READY_FILE" ]] && break
        ! is_running && exit 1
        sleep 0.1
    done
    [[ ! -f "\$READY_FILE" ]] && exit 1
fi

INPUT_TEXT=\$(timeout 2 wl-paste 2>/dev/null || true)
if [[ -n "\$INPUT_TEXT" ]]; then
    CLEAN_TEXT=\$(printf '%s' "\$INPUT_TEXT" | tr '\n' ' ')
    printf '%s\n' "\$CLEAN_TEXT" > "\$FIFO_PATH" &
    WRITE_PID=\$!
    
    # Wait for write (non-blocking validation)
    WRITE_OK=false
    for _ in {1..20}; do
        if ! kill -0 "\$WRITE_PID" 2>/dev/null; then
            wait "\$WRITE_PID" 2>/dev/null && WRITE_OK=true
            break
        fi
        sleep 0.1
    done
    
    if \$WRITE_OK; then
        notify -t 1000 "Kokoro" "Processing..."
    else
        kill "\$WRITE_PID" 2>/dev/null || true
        notify "Kokoro Error" "Daemon Unresponsive"
    fi
else
    notify "Kokoro" "Clipboard empty"
fi
EOF

chmod +x "$TARGET_TRIGGER"
echo ":: Setup Complete. Trigger script installed at:"
echo "   $TARGET_TRIGGER"
