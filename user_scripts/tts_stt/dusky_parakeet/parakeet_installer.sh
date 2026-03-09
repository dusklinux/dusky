#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# PARAKEET STT INSTALLER (Silent Fetch, CuFFT, and MIGraphX AMD Support)
# ==============================================================================

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly ENV_DIR="$HOME/contained_apps/uv/dusky_stt"
readonly TARGET_TRIGGER="$SCRIPT_DIR/trigger.sh"

echo ":: Initializing Parakeet STT Setup..."

echo "Select your installation target:"
echo "  1) NVIDIA (CUDA) - Best for GeForce/RTX cards"
echo "  2) AMD (ROCm)    - Best for Radeon/Instinct cards"
echo "  3) CPU Only      - Fallback"
read -rp "Enter choice [1-3]: " HW_CHOICE || true

MODE="cpu"
[[ "$HW_CHOICE" == "1" ]] && MODE="nvidia"
[[ "$HW_CHOICE" == "2" ]] && MODE="amd"

mkdir -p "$ENV_DIR"
cp "$SCRIPT_DIR/dusky_stt_main.py" "$ENV_DIR/"

cd "$ENV_DIR"
uv init --python 3.14 --no-workspace 2>/dev/null || true

echo ":: Installing Dependencies for $MODE..."
uv add "onnx-asr" "soundfile" "numpy" "huggingface_hub" "hf-transfer"

case "$MODE" in
    nvidia)
        uv add "onnxruntime-gpu" "nvidia-cuda-runtime-cu12" "nvidia-cudnn-cu12" "nvidia-cufft-cu12"
        ;;
    amd)
        uv pip install onnxruntime-rocm onnxruntime-migraphx --force-reinstall --no-deps
        ;;
    cpu)
        uv add "onnxruntime"
        ;;
esac

echo ":: Pre-fetching ONLY the quantized INT8 model via hf_transfer (Rust Engine)..."
env CUDA_VISIBLE_DEVICES="-1" HF_HUB_ENABLE_HF_TRANSFER="1" \
uv run --no-sync python -c "
import onnx_asr
onnx_asr.load_model('nemo-parakeet-tdt-0.6b-v2', quantization='int8')
"

echo ":: Generating Toggle Trigger Script at $TARGET_TRIGGER..."
cat << 'EOF' > "$TARGET_TRIGGER"
#!/usr/bin/env bash

readonly APP_DIR="$HOME/contained_apps/uv/dusky_stt"
readonly PID_FILE="/tmp/dusky_stt.pid"
readonly READY_FILE="/tmp/dusky_stt.ready"
readonly FIFO_PATH="/tmp/dusky_stt.fifo"
readonly DAEMON_LOG="/tmp/dusky_stt.log"
readonly RECORD_PID_FILE="/tmp/dusky_stt_record.pid"
readonly YAD_PID_FILE="/tmp/dusky_stt_yad.pid"

AUDIO_DIR="/mnt/zram1/parakeet_mic"
[[ ! -d "/mnt/zram1" ]] && AUDIO_DIR="/tmp/dusky_stt_audio"
readonly AUDIO_FILE="$AUDIO_DIR/stt_current.wav"

get_libs() {
    local SITE_PACKAGES
    SITE_PACKAGES=$(find "$APP_DIR/.venv" -type d -name "site-packages" 2>/dev/null | head -n 1)
    if [[ -n "$SITE_PACKAGES" && -d "$SITE_PACKAGES/nvidia" ]]; then
        find "$SITE_PACKAGES/nvidia" -type d -name "lib" | tr '\n' ':' | sed 's/:$//'
    fi
}

is_running() { [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE" 2>/dev/null)" 2>/dev/null; }

start_daemon() {
    rm -f "$READY_FILE"

    local EXTRA_LIBS
    EXTRA_LIBS=$(get_libs)
    local CURRENT_LD="${LD_LIBRARY_PATH:-}"
    [[ -n "$EXTRA_LIBS" ]] && CURRENT_LD="${EXTRA_LIBS}:${CURRENT_LD}"

    cd "$APP_DIR"
    env LD_LIBRARY_PATH="$CURRENT_LD" nohup uv run --no-sync dusky_stt_main.py --daemon > "$DAEMON_LOG" 2>&1 &
    echo $! > "$PID_FILE"

    for _ in {1..300}; do
        [[ -f "$READY_FILE" ]] && return 0
        sleep 0.1
    done
    return 1
}

stop_recording() {
    [[ -f "$RECORD_PID_FILE" ]] || return 0

    local RECORD_PID
    RECORD_PID=$(cat "$RECORD_PID_FILE" 2>/dev/null)
    rm -f "$RECORD_PID_FILE"

    if [[ -f "$YAD_PID_FILE" ]]; then
        local YAD_PID
        YAD_PID=$(cat "$YAD_PID_FILE" 2>/dev/null)
        rm -f "$YAD_PID_FILE"
        [[ -n "$YAD_PID" ]] && kill "$YAD_PID" 2>/dev/null || true
    fi

    if [[ -n "$RECORD_PID" ]]; then
        kill -15 "$RECORD_PID" 2>/dev/null || true
        local i
        for i in {1..40}; do
            kill -0 "$RECORD_PID" 2>/dev/null || break
            sleep 0.05
        done
        kill -9 "$RECORD_PID" 2>/dev/null || true
    fi

    notify-send -a "Parakeet STT" -t 1500 "Processing..." "Transcribing to clipboard"
    printf '%s\n' "$AUDIO_FILE" > "$FIFO_PATH" &
}

kill_active_session() {
    if [[ -f "$RECORD_PID_FILE" ]]; then
        kill -15 "$(cat "$RECORD_PID_FILE" 2>/dev/null)" 2>/dev/null || true
        rm -f "$RECORD_PID_FILE"
    fi
    if [[ -f "$YAD_PID_FILE" ]]; then
        kill -15 "$(cat "$YAD_PID_FILE" 2>/dev/null)" 2>/dev/null || true
        rm -f "$YAD_PID_FILE"
    fi
}

show_help() {
    cat << 'HELP'
Parakeet STT — Trigger Script

USAGE:
    trigger.sh              Toggle recording (starts daemon if needed)
    trigger.sh [OPTION]

OPTIONS:
    --help, -h       Show this help
    --kill, --stop   Stop the daemon
    --restart        Restart the daemon
    --status         Check if daemon is running
    --logs           Tail the daemon log
HELP
}

case "${1:-}" in
    --help|-h) show_help; exit 0 ;;
    --kill|--stop)
        kill_active_session
        if is_running; then
            kill -15 "$(cat "$PID_FILE")" 2>/dev/null || true
            echo ":: Daemon stopped."
        else
            echo ":: Daemon not running."
        fi
        rm -f "$PID_FILE" "$FIFO_PATH" "$READY_FILE"
        exit 0
        ;;
    --status)
        if is_running; then
            echo ":: Daemon running (PID: $(cat "$PID_FILE"))"
        else
            echo ":: Daemon not running."
        fi
        exit 0
        ;;
    --restart)
        echo ":: Restarting daemon..."
        kill_active_session
        if is_running; then
            kill -15 "$(cat "$PID_FILE")" 2>/dev/null || true
        fi
        rm -f "$PID_FILE" "$FIFO_PATH" "$READY_FILE"
        start_daemon && echo ":: Daemon restarted."
        exit $?
        ;;
    --logs)
        if [[ -f "$DAEMON_LOG" ]]; then
            tail -f "$DAEMON_LOG"
        else
            echo ":: No log file at $DAEMON_LOG"
        fi
        exit 0
        ;;
    "") ;;
    *) echo ":: Unknown flag: $1"; exit 1 ;;
esac

if [[ -f "$RECORD_PID_FILE" ]] && kill -0 "$(cat "$RECORD_PID_FILE" 2>/dev/null)" 2>/dev/null; then
    stop_recording
else
    if ! is_running; then
        start_daemon || { notify-send -a "Parakeet STT" -u critical "STT Error" "Daemon startup failed"; exit 1; }
    fi

    mkdir -p "$AUDIO_DIR"
    if command -v pw-record &>/dev/null; then
        pw-record --target @DEFAULT_AUDIO_SOURCE@ --rate 16000 --channels 1 --format=s16 "$AUDIO_FILE" &
    else
        arecord -f S16_LE -c 1 -r 16000 "$AUDIO_FILE" &
    fi
    echo $! > "$RECORD_PID_FILE"

    notify-send -a "Parakeet STT" -t 2500 "Listening..." "Speak now. Trigger again to stop."

    if command -v yad &>/dev/null; then
        yad --title="Parakeet STT" \
            --text="\n  🎙️ Recording in progress...  \n" \
            --button="Stop Recording:0" \
            --on-top \
            --skip-taskbar \
            --center \
            --width=300 \
            --no-escape \
            --fixed &
        YAD_PID=$!

        sleep 0.1
        if kill -0 "$YAD_PID" 2>/dev/null; then
            echo "$YAD_PID" > "$YAD_PID_FILE"
            (
                while kill -0 "$YAD_PID" 2>/dev/null; do sleep 0.2; done
                [[ -f "$RECORD_PID_FILE" ]] && stop_recording
            ) &
            disown
        fi
    fi
fi
EOF

chmod +x "$TARGET_TRIGGER"
echo ":: Setup Complete! Bind a hotkey to $TARGET_TRIGGER"
if ! command -v yad &>/dev/null; then
    echo ":: NOTE: Install 'yad' for a graphical Stop Recording button (optional)"
fi
