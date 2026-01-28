#!/bin/bash
#
# Name: transcribe_voice_parakeet
# Version: 1.3 (Inference Mode & Robust Arg Parsing)

# --- Strict Mode ---
set -euo pipefail

# --- Configuration ---
readonly VENV_PATH="/home/dusk/contained_apps/uv/parakeet/"
readonly PYTHON_SCRIPT="/home/dusk/user_scripts/tts_stt/parakeet/transcribe_parakeet.py"
readonly AUDIO_DIR="/mnt/zram1/mic"

# --- Logging ---
readonly LOG_FILE="/tmp/transcribe_voice_parakeet.log"
readonly PYTHON_OUTPUT_TEMP_FILE="/tmp/transcription.output"
readonly PYTHON_LOG_TEMP_FILE="/tmp/transcription.log"

# Clear previous logs
: > "$LOG_FILE"
: > "$PYTHON_OUTPUT_TEMP_FILE"
: > "$PYTHON_LOG_TEMP_FILE"

FFMPEG_PID=""

# --- Functions ---

log_message() {
    echo -e "[$(date '+%T')] $1" | tee -a "$LOG_FILE" >&2
}

cleanup() {
    local exit_status=$?
    # Kill FFMPEG if it's still running
    if [[ -n "$FFMPEG_PID" ]]; then
        if kill -0 "$FFMPEG_PID" 2>/dev/null; then
            kill "$FFMPEG_PID" 2>/dev/null || true
        fi
    fi
    # If we failed, show the log
    if [[ $exit_status -ne 0 ]]; then
        log_message "Exiting with error code $exit_status. See $LOG_FILE"
    fi
    exit $exit_status
}

fatal_error_dialog() {
    local error_details="$1"
    # Escape special chars for Pango markup in Yad
    local escaped_details
    escaped_details=$(echo -n "$error_details" | sed 's/&/\&/g; s/</\</g; s/>/\>/g')
    
    yad --title="Fatal Transcription Error" \
        --text="<span color='red'><b>Error</b></span>\n\n$escaped_details" \
        --width=500 --height=400 --button="Close:1" \
        --text-info --wrap \
        < <(echo -e "Log file: $LOG_FILE")
    exit 1
}

trap cleanup EXIT SIGINT SIGTERM

# --- Main ---

mkdir -p "$AUDIO_DIR"

# 1. Determine Next Filename (Robust Numbering)
last_num=0
shopt -s nullglob
for f in "$AUDIO_DIR"/*.wav; do
    base_f=$(basename -- "$f" .wav)
    # Check if filename is an integer
    if [[ "$base_f" =~ ^[0-9]+$ ]]; then
        # Compare integers
        if (( 10#$base_f > 10#$last_num )); then
            last_num=$base_f
        fi
    fi
done
shopt -u nullglob

next_num=$((last_num + 1))
readonly AUDIO_FILE="${AUDIO_DIR}/${next_num}.wav"

# 2. Record Audio
DEFAULT_SOURCE=$(pactl get-default-source)
log_message "Recording to: $AUDIO_FILE using $DEFAULT_SOURCE"

# Start FFMPEG in background
ffmpeg -y -f pulse -i "$DEFAULT_SOURCE" -ac 1 "$AUDIO_FILE" -loglevel error &
FFMPEG_PID=$!

# 3. User Interface (Stop Button)
yad --title="Parakeet" --text="<span size='large'><b>🔴 Recording...</b></span>" \
    --width=300 --height=100 --button="Stop:0" --fixed --center --undecorated

# 4. Stop Recording
kill -SIGINT "$FFMPEG_PID"
wait "$FFMPEG_PID" 2>/dev/null || true
FFMPEG_PID=""

# 5. Transcribe
log_message "Transcribing..."

# Optimization: Help PyTorch handle memory fragmentation
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

py_exit_code=0
# Execute Python Script
"${VENV_PATH}bin/python3" -u "$PYTHON_SCRIPT" "$AUDIO_FILE" > "$PYTHON_OUTPUT_TEMP_FILE" 2> "$PYTHON_LOG_TEMP_FILE" || py_exit_code=$?

# 6. Process Result
if [[ $py_exit_code -ne 0 ]]; then
    log_message "Python Script Failed:"
    cat "$PYTHON_LOG_TEMP_FILE" >> "$LOG_FILE"
    fatal_error_dialog "Transcription script failed. Check log."
fi

FINAL_TEXT=$(<"$PYTHON_OUTPUT_TEMP_FILE")

if [[ -n "$FINAL_TEXT" ]]; then
    echo -n "$FINAL_TEXT" | wl-copy
    notify-send "Parakeet" "Text copied to clipboard." -t 2000
    log_message "Success: $FINAL_TEXT"
else
    notify-send "Parakeet" "No text detected." -t 2000
    log_message "Result was empty."
fi

exit 0
