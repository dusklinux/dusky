#!/usr/bin/env bash
#
# Name: transcribe_voice_parakeet
# Version: 3.2 (Syntax Fix)
#
# Description: Records microphone audio, transcribes it using a local Parakeet
#              ASR model, and copies the result to the Wayland clipboard.

# --- Strict Mode & Locale ---
set -euo pipefail
shopt -s nullglob
# Use C.UTF-8 to allow Emojis in YAD while keeping safe data formats.
export LC_ALL=C.UTF-8

# --- Cleanup Trap (Set FIRST) ---
# This must be defined and trapped before any file operations.
FFMPEG_PID=""
PYTHON_OUTPUT_TEMP_FILE=""
PYTHON_LOG_TEMP_FILE=""

cleanup() {
    local exit_status=$?

    # Kill ffmpeg if still running
    if [[ -n "${FFMPEG_PID:-}" ]] && kill -0 "$FFMPEG_PID" 2>/dev/null; then
        kill -SIGINT "$FFMPEG_PID" 2>/dev/null || true
        wait "$FFMPEG_PID" 2>/dev/null || true
    fi

    # Clean up temporary files (ignore errors)
    rm -f -- "${PYTHON_OUTPUT_TEMP_FILE:-}" "${PYTHON_LOG_TEMP_FILE:-}"

    # Note: LOG_FILE is preserved for post-mortem debugging.
    exit "$exit_status"
}
trap cleanup EXIT INT TERM

# --- Dynamic Configuration ---
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
readonly VENV_PATH="${HOME}/contained_apps/uv/parakeet"
readonly AUDIO_DIR="${XDG_RUNTIME_DIR:-/tmp}/parakeet_mic"
readonly LOG_FILE="/tmp/transcribe_voice_parakeet.log"

# Python script location: prefer co-located, fallback to legacy path
if [[ -f "${SCRIPT_DIR}/transcribe_parakeet.py" ]]; then
    readonly PYTHON_SCRIPT="${SCRIPT_DIR}/transcribe_parakeet.py"
else
    readonly PYTHON_SCRIPT="${HOME}/user_scripts/tts_stt/parakeet/transcribe_parakeet.py"
fi

# --- Create Secure Temp Files ---
PYTHON_OUTPUT_TEMP_FILE=$(mktemp /tmp/transcription.XXXXXX.output)
PYTHON_LOG_TEMP_FILE=$(mktemp /tmp/transcription.XXXXXX.log)

# Initialize main log file
: > "$LOG_FILE"

# --- Functions ---
log_message() {
    local msg
    printf -v msg '[%s] %s' "$(date '+%T')" "$1"
    printf '%s\n' "$msg" >&2
    printf '%s\n' "$msg" >> "$LOG_FILE"
}

show_fatal_error_dialog() {
    local error_details="$1"

    # Escape for Pango/XML markup
    local escaped_details="${error_details//&/&amp;}"
    escaped_details="${escaped_details//</&lt;}"
    escaped_details="${escaped_details//>/&gt;}"

    yad --title="Fatal Transcription Error" \
        --text="<span color='red'><b>Error:</b></span> ${escaped_details}" \
        --width=500 --height=400 \
        --button="Close:1" \
        --text-info --wrap < "$LOG_FILE"

    exit 1
}

# --- Dependency Validation ---
missing_deps=()
for cmd in ffmpeg pactl yad wl-copy notify-send; do
    command -v "$cmd" &>/dev/null || missing_deps+=("$cmd")
done

if (( ${#missing_deps[@]} > 0 )); then
    log_message "Error: Missing required commands: ${missing_deps[*]}"
    exit 1
fi

# --- Path Validation ---
if [[ ! -x "${VENV_PATH}/bin/python3" ]]; then
    log_message "Error: Python interpreter not found at: ${VENV_PATH}/bin/python3"
    exit 1
fi

if [[ ! -f "$PYTHON_SCRIPT" ]]; then
    log_message "Error: Python script not found."
    log_message "Searched: ${SCRIPT_DIR}/transcribe_parakeet.py"
    log_message "Searched: ${HOME}/user_scripts/tts_stt/parakeet/transcribe_parakeet.py"
    exit 1
fi

# --- Main Logic ---

# 1. Create audio directory
if ! mkdir -p "$AUDIO_DIR"; then
    log_message "Error: Failed to create audio directory: $AUDIO_DIR"
    exit 1
fi

# 2. Determine Next Filename (Robust Base-10 Numbering)
last_num=0
for f in "$AUDIO_DIR"/*.wav; do
    # Pure Bash: remove path and extension
    local_base="${f##*/}"
    local_base="${local_base%.wav}"

    if [[ "$local_base" =~ ^[0-9]+$ ]]; then
        # Force base-10 to prevent octal interpretation (e.g., 08, 09)
        current_num=$((10#$local_base))
        (( current_num > last_num )) && last_num=$current_num
    fi
done

next_num=$((last_num + 1))
readonly AUDIO_FILE="${AUDIO_DIR}/${next_num}.wav"

# 3. Get Default Audio Source
if ! DEFAULT_SOURCE=$(pactl get-default-source 2>/dev/null) || [[ -z "$DEFAULT_SOURCE" ]]; then
    log_message "Error: Could not determine default PulseAudio source."
    exit 1
fi
log_message "Recording to: ${AUDIO_FILE} | Source: ${DEFAULT_SOURCE}"

# 4. Start Recording
ffmpeg -y -f pulse -i "$DEFAULT_SOURCE" \
    -ar 16000 -ac 1 \
    -loglevel error \
    "$AUDIO_FILE" &
FFMPEG_PID=$!

# Allow ffmpeg to initialize, then verify
sleep 0.3
if ! kill -0 "$FFMPEG_PID" 2>/dev/null; then
    wait "$FFMPEG_PID" 2>/dev/null || true
    log_message "Error: ffmpeg failed to start. Check audio source."
    exit 1
fi

# 5. Show Stop Button (Blocking GUI)
yad --title="Parakeet" \
    --text='<span size="large"><b>🔴 Recording...</b></span>' \
    --width=300 --height=100 \
    --button="Stop:0" \
    --fixed --center --on-top --undecorated

# 6. Stop Recording Gracefully
if [[ -n "$FFMPEG_PID" ]] && kill -0 "$FFMPEG_PID" 2>/dev/null; then
    kill -SIGINT "$FFMPEG_PID" 2>/dev/null || true
    # Wait up to 2 seconds for ffmpeg to exit
    # FIXED: removed 'local' keyword here as this is main scope
    i=0
    while (( i++ < 20 )) && kill -0 "$FFMPEG_PID" 2>/dev/null; do
        sleep 0.1
    done
    wait "$FFMPEG_PID" 2>/dev/null || true
fi
FFMPEG_PID="" # Clear to prevent cleanup from re-killing

# 7. Validate Recording Output
if [[ ! -f "$AUDIO_FILE" ]]; then
    log_message "Error: Audio file was not created."
    notify-send -u critical "Parakeet" "Recording failed - no file created"
    exit 1
fi

if [[ ! -s "$AUDIO_FILE" ]]; then
    log_message "Error: Audio file is empty (0 bytes)."
    notify-send -u critical "Parakeet" "Recording failed - empty file"
    rm -f -- "$AUDIO_FILE"
    exit 1
fi
log_message "Recording complete: $(stat -c%s "$AUDIO_FILE") bytes"

# 8. Transcribe Audio
log_message "Starting transcription..."
export PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True"

py_exit_code=0
"${VENV_PATH}/bin/python3" -u "$PYTHON_SCRIPT" "$AUDIO_FILE" \
    > "$PYTHON_OUTPUT_TEMP_FILE" \
    2> "$PYTHON_LOG_TEMP_FILE" \
    || py_exit_code=$?

# 9. Process Transcription Result
if [[ $py_exit_code -ne 0 ]]; then
    log_message "Python script failed with exit code: $py_exit_code"
    [[ -s "$PYTHON_LOG_TEMP_FILE" ]] && cat -- "$PYTHON_LOG_TEMP_FILE" >> "$LOG_FILE"
    show_fatal_error_dialog "Transcription failed (exit code: ${py_exit_code}). Check log."
fi

FINAL_TEXT=$(<"$PYTHON_OUTPUT_TEMP_FILE")

if [[ -n "$FINAL_TEXT" ]]; then
    printf '%s' "$FINAL_TEXT" | wl-copy
    notify-send "Parakeet" "Transcription copied to clipboard." -t 2000
    log_message "Success: ${FINAL_TEXT}"
else
    notify-send "Parakeet" "No speech detected." -t 2000
    log_message "Result: Empty (silence or no speech)."
fi

exit 0
