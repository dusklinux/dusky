#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# DUSKY STT INSTALLER (Faster-Whisper Edition)
# Arch Linux / Wayland / Hyprland Optimized
# Strict NVIDIA + CPU Support with Classy YAD Integration
# ==============================================================================

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly ENV_DIR="$HOME/contained_apps/uv/dusky_stt"
readonly MODEL_DIR="$ENV_DIR/models"

# FIX: Target trigger stays exactly in the directory where you run the installer.
readonly TARGET_TRIGGER="$SCRIPT_DIR/trigger.sh"
DEFAULT_MODEL="distil-large-v3"

echo ":: Initializing Dusky STT Setup..."

# --- 1. System Dependencies Check ---
echo ":: Checking System Dependencies..."
for cmd in uv pw-record yad; do
    if ! command -v "$cmd" &>/dev/null; then
        echo ":: ERROR: Missing critical dependency '$cmd'. Please install it."
        echo "   (Hint: sudo pacman -S yad)"
        exit 1
    fi
done

if ! command -v wl-copy &>/dev/null; then
    echo ":: WARNING: 'wl-copy' not found. Clipboard functionality will be disabled."
fi

if ! command -v wtype &>/dev/null && ! command -v ydotool &>/dev/null; then
    echo ":: WARNING: Neither 'wtype' nor 'ydotool' found. Auto-typing disabled."
    echo "   The script will still copy text to your clipboard."
fi

# --- 2. Hardware Detection Report ---
echo "--------------------------------------------------------"
echo ":: Hardware Scan:"
GPU_FOUND=false

if command -v lspci &>/dev/null; then
    if lspci | grep -E "VGA|3D|Display" | grep -i "nvidia" &>/dev/null; then
        echo "   [✓] NVIDIA GPU Detected"
        GPU_FOUND=true
    fi
    if lspci | grep -E "VGA|3D|Display" | grep -iE "amd|radeon" &>/dev/null; then
        echo "   [!] AMD GPU Detected (CTranslate2 lacks ROCm; falling back to CPU)"
    fi
fi

if [ "$GPU_FOUND" = false ]; then
    echo "   [!] No dedicated NVIDIA GPU detected."
fi
echo "--------------------------------------------------------"

# --- 3. User Selection ---
echo "Select your Faster-Whisper acceleration mode:"
echo "  1) NVIDIA (CUDA) - INT8_Float16, High Performance"
echo "  2) CPU Only      - Fallback, INT8 mode"
echo ""

HW_CHOICE=""
read -p "Enter choice [1-2]: " HW_CHOICE || true

MODE="cpu"
if [[ "$HW_CHOICE" == "1" ]]; then MODE="nvidia"
elif [[ "$HW_CHOICE" == "2" ]]; then MODE="cpu"
else
    echo ":: Invalid choice. Defaulting to CPU mode."
    MODE="cpu"
fi

echo ":: Selected Mode: ${MODE^^}"

# --- 3.5. Model Selection ---
echo "--------------------------------------------------------"
echo "Select your default Whisper Model:"
echo "  1) tiny.en          (Fastest, ~300MB VRAM)"
echo "  2) base.en          (~450MB VRAM)"
echo "  3) small.en         (Balanced, ~900MB VRAM)"
echo "  4) medium.en        (High Accuracy, ~2.3GB VRAM)"
echo "  5) distil-large-v3  (Best overall, ~2.2GB VRAM)"
echo ""

MODEL_CHOICE=""
read -p "Enter choice [1-5] (Default: 5): " MODEL_CHOICE || true

case "$MODEL_CHOICE" in
    1) DEFAULT_MODEL="tiny.en" ;;
    2) DEFAULT_MODEL="base.en" ;;
    3) DEFAULT_MODEL="small.en" ;;
    4) DEFAULT_MODEL="medium.en" ;;
    5|"") DEFAULT_MODEL="distil-large-v3" ;;
    *) echo ":: Invalid choice. Defaulting to distil-large-v3."; DEFAULT_MODEL="distil-large-v3" ;;
esac
echo ":: Selected Default Model: $DEFAULT_MODEL"

# --- 4. Environment Setup ---
mkdir -p "$ENV_DIR" "$MODEL_DIR"

if [[ -f "$SCRIPT_DIR/dusky_stt_main.py" ]]; then
    cp "$SCRIPT_DIR/dusky_stt_main.py" "$ENV_DIR/"
    echo ":: dusky_stt_main.py deployed."
else
    echo ":: ERROR: dusky_stt_main.py not found in current directory."
    exit 1
fi

cd "$ENV_DIR"
echo ":: Configuring Python Environment..."
uv init --python 3.14 --no-workspace 2>/dev/null || uv init --no-workspace 2>/dev/null || true

# --- 5. Conditional Dependency Installation ---
echo ":: Installing Dependencies for $MODE..."

case "$MODE" in
    nvidia)
        uv add "faster-whisper" "nvidia-cublas-cu12" "nvidia-cudnn-cu12" "nvidia-cuda-runtime-cu12"
        ;;
    cpu)
        uv add "faster-whisper"
        ;;
esac

# --- 6. Explicit Model Synchronization (IDEMPOTENT) ---
echo ""
echo "========================================================"
echo ":: PRE-FETCHING AI MODEL TO LOCAL CACHE"
echo ":: Model: $DEFAULT_MODEL"
echo ":: Destination: $MODEL_DIR"
echo ":: If the model exists, it will instantly verify hashes."
echo ":: If missing, it will display a download progress bar."
echo "========================================================"
uv run python -c "
import sys
from faster_whisper import download_model
try:
    print('Checking local model cache...')
    download_model('$DEFAULT_MODEL', cache_dir='$MODEL_DIR')
    print('✓ Model is present and fully verified.')
except Exception as e:
    print(f'❌ Failed to download model: {e}')
    sys.exit(1)
"
echo "========================================================"
echo ""

# --- 7. Generate Trigger Script ---
echo ":: Generating Trigger Script in $SCRIPT_DIR..."

cat << EOF > "$TARGET_TRIGGER"
#!/usr/bin/env bash
# Dusky STT Trigger ($MODE edition)
# Toggle behavior: First run starts recording. Second run stops recording and transcribes.

readonly APP_DIR="$HOME/contained_apps/uv/dusky_stt"
readonly PID_FILE="/tmp/dusky_stt.pid"
readonly READY_FILE="/tmp/dusky_stt.ready"
readonly FIFO_PATH="/tmp/dusky_stt.fifo"
readonly DAEMON_LOG="/tmp/dusky_stt.log"
readonly DEBUG_LOG="\$APP_DIR/dusky_stt_debug.log"
readonly INSTALL_MODE="$MODE"

# Recording vars
readonly RECORD_PID_FILE="/tmp/dusky_stt_record.pid"
readonly YAD_PID_FILE="/tmp/dusky_stt_yad.pid"
readonly AUDIO_TMP_FILE="/tmp/dusky_stt_capture.wav"
DEFAULT_MODEL="$DEFAULT_MODEL"

# --- Helpers ---
get_libs() {
    if [[ "\$INSTALL_MODE" == "nvidia" ]]; then
        local SITE_PACKAGES
        SITE_PACKAGES=\$(find "\$APP_DIR/.venv" -type d -name "site-packages" 2>/dev/null | head -n 1)
        if [[ -n "\$SITE_PACKAGES" && -d "\$SITE_PACKAGES/nvidia" ]]; then
            # Extract paths for cublas, cudnn, and cudart injected via uv
            find "\$SITE_PACKAGES/nvidia" -type d -name "lib" | tr '\n' ':' | sed 's/:$//'
        fi
    fi
}

notify() { notify-send "\$@" 2>/dev/null || true; }

is_running() { [[ -f "\$PID_FILE" ]] && kill -0 "\$(cat "\$PID_FILE" 2>/dev/null)" 2>/dev/null; }

stop_daemon() {
    if [[ -f "\$PID_FILE" ]]; then
        local pid=\$(cat "\$PID_FILE" 2>/dev/null)
        if [[ -n "\$pid" ]]; then
            kill "\$pid" 2>/dev/null || true
            sleep 1
            kill -9 "\$pid" 2>/dev/null || true
        fi
    fi
    rm -f "\$PID_FILE" "\$FIFO_PATH" "\$READY_FILE" "\$RECORD_PID_FILE" "\$YAD_PID_FILE"
}

start_daemon() {
    local debug_mode="\${1:-false}"
    
    local EXTRA_LIBS=\$(get_libs)
    if [[ -n "\$EXTRA_LIBS" ]]; then
        export LD_LIBRARY_PATH="\${EXTRA_LIBS}\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
    fi

    cd "\$APP_DIR"
    if [[ "\$debug_mode" == "true" ]]; then
        export DUSKY_STT_LOG_LEVEL="DEBUG"
        export DUSKY_STT_LOG_FILE="\$DEBUG_LOG"
        nohup uv run dusky_stt_main.py --daemon --mode "\$INSTALL_MODE" --debug-file "\$DEBUG_LOG" > "\$DAEMON_LOG" 2>&1 &
    else
        nohup uv run dusky_stt_main.py --daemon --mode "\$INSTALL_MODE" > "\$DAEMON_LOG" 2>&1 &
    fi

    local daemon_pid=\$!
    echo "\$daemon_pid" > "\$PID_FILE"

    for _ in {1..150}; do
        if [[ -f "\$READY_FILE" ]]; then return 0; fi
        if ! kill -0 "\$daemon_pid" 2>/dev/null; then return 1; fi
        sleep 0.2
    done
    return 1
}

show_help() {
    cat << 'HELP'
Dusky STT — Trigger Script
USAGE:
    ./trigger.sh                   (Toggle record/transcribe)
    ./trigger.sh --model <name>    (Use specific model)
    ./trigger.sh --kill            (Stop the background daemon)
    ./trigger.sh --restart         (Restart the background daemon)
    ./trigger.sh --debug           (Start daemon in debug mode)
    ./trigger.sh --logs            (Tail daemon logs)

MODELS: tiny.en, base.en, small.en, medium.en, distil-large-v3
HELP
}

# --- CLI Logic ---
MODEL="\$DEFAULT_MODEL"

while [[ \$# -gt 0 ]]; do
    case "\$1" in
        --help|-h) show_help; exit 0 ;;
        --kill) stop_daemon; echo ":: Daemon stopped."; exit 0 ;;
        --model|-m) MODEL="\$2"; shift 2 ;;
        --logs) tail -f "\$DAEMON_LOG"; exit 0 ;;
        --debug) 
            stop_daemon; 
            echo ":: Starting Daemon in Debug Mode..."
            start_daemon "true"; 
            tail -f "\$DEBUG_LOG"; 
            exit \$? ;;
        --restart) 
            stop_daemon; 
            echo ":: Restarting Daemon..."
            start_daemon "false"; 
            exit \$? ;;
        *) echo "Unknown flag: \$1"; exit 1 ;;
    esac
done

# Ensure daemon is running
if ! is_running; then
    rm -f "\$FIFO_PATH" "\$PID_FILE" "\$READY_FILE"
    echo ":: Daemon not running. Booting it up..."
    if ! start_daemon "false"; then echo ":: ERROR: Daemon failed to start"; exit 1; fi
fi

# --- Audio Toggle Logic ---
if [[ -f "\$RECORD_PID_FILE" ]] && kill -0 "\$(cat "\$RECORD_PID_FILE")" 2>/dev/null; then
    # WE ARE RECORDING -> STOP & TRANSCRIBE (Second Hotkey Press)
    REC_PID=\$(cat "\$RECORD_PID_FILE")
    kill -INT "\$REC_PID" 2>/dev/null || true
    rm -f "\$RECORD_PID_FILE"
    
    # Gracefully tear down the YAD UI Subshell
    if [[ -f "\$YAD_PID_FILE" ]]; then
        kill "\$(cat "\$YAD_PID_FILE")" 2>/dev/null || true
        rm -f "\$YAD_PID_FILE"
    fi
    pkill -f "yad --title=Dusky STT" 2>/dev/null || true
    
    echo -e ":: 🟢 Stopping recording. Transcribing with \${MODEL}..."
    
    # Send payload to FIFO
    printf "%s|%s\n" "\$AUDIO_TMP_FILE" "\$MODEL" > "\$FIFO_PATH" &
else
    # NOT RECORDING -> START CAPTURE
    rm -f "\$AUDIO_TMP_FILE"
    echo -e ":: 🔴 Recording Started! Use hotkey again or click popup to stop."
    
    # Use Pipewire native recorder
    pw-record --target auto "\$AUDIO_TMP_FILE" &
    REC_PID=\$!
    echo \$REC_PID > "\$RECORD_PID_FILE"

    # Launch elegant, non-blocking YAD popup in a subshell
    (
        yad_exit=0
        yad --title="Dusky STT" \\
            --text="<span font='13' foreground='#ff4a4a'><b>🎙️ Recording Audio</b></span>\n<span font='10' foreground='#999999'>Press shortcut again or click below</span>" \\
            --button="Transcribe:0" \\
            --button="Cancel:1" \\
            --width=280 \\
            --borders=16 \\
            --undecorated --on-top --fixed --center --skip-taskbar 2>/dev/null || yad_exit=\$?
        
        # Guard: Only evaluate button clicks if the main hotkey didn't already cancel this subshell
        if [[ -f "\$RECORD_PID_FILE" ]] && kill -0 "\$(cat "\$RECORD_PID_FILE")" 2>/dev/null; then
            if [ \$yad_exit -eq 0 ]; then
                # User clicked 'Transcribe' -> Feed back into script logic
                "\$0" --model "\$MODEL"
            else
                # User pressed ESC or 'Cancel' -> Abort completely
                kill -INT "\$(cat "\$RECORD_PID_FILE")" 2>/dev/null || true
                rm -f "\$RECORD_PID_FILE" "\$AUDIO_TMP_FILE" "\$YAD_PID_FILE"
                notify -t 1500 "Dusky STT" "Recording Cancelled."
            fi
        fi
    ) &
    YAD_PID=\$!
    echo \$YAD_PID > "\$YAD_PID_FILE"
fi
EOF

chmod +x "$TARGET_TRIGGER"
echo ":: Setup Complete. Trigger script installed exactly at:"
echo "   $TARGET_TRIGGER"
echo ""
echo ":: Try it out by running: ./trigger.sh"
