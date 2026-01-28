#!/bin/bash
#
# Name: transcribe_voice
#
# Description: A completely re-architected, robust script to record audio,
#              transcribe it, and copy the result to the clipboard.
#              This version features extensive, mandatory logging to a
#              dedicated file for unparalleled debugging and transparency.
#
# Date: 2025-06-11
# Version: 2.1 (Explicit Python Interpreter Invocation)
#
# Dependencies: bash, ffmpeg, yad, wl-copy, pactl, notify-send

# --- Strict Mode & Configuration ---
set -euo pipefail
#
# --- User-configurable Constants ---
readonly VENV_PATH="/home/dusk/contained_apps/uv/fasterwhisper_cpu/"
readonly PYTHON_SCRIPT="/home/dusk/user_scripts/tts_stt/faster_whisper/config.py"
readonly AUDIO_DIR="/mnt/zram1/mic"

# --- Logging and Runtime Configuration ---
readonly LOG_FILE="/tmp/transcribe_voice.log"
readonly PYTHON_OUTPUT_TEMP_FILE="/tmp/transcription.output"
readonly PYTHON_LOG_TEMP_FILE="/tmp/transcription.log"
>"$LOG_FILE"
>"$PYTHON_OUTPUT_TEMP_FILE"
>"$PYTHON_LOG_TEMP_FILE"

# --- Script Globals ---
YAD_PID=""
FFMPEG_PID=""

# --- Function Definitions ---

log_message() {
    local message="$1"
    echo -e "[$(date '+%F %T')] $message" | tee -a "$LOG_FILE" >&2
}

cleanup() {
    local exit_status=$?
    log_message "--- Running cleanup ---"
    if [[ -n "$YAD_PID" && -e "/proc/$YAD_PID" ]]; then
        log_message "Killing YAD process: $YAD_PID"
        kill "$YAD_PID" 2>/dev/null || true
    fi
    if [[ -n "$FFMPEG_PID" && -e "/proc/$FFMPEG_PID" ]]; then
        log_message "Killing FFMPEG process: $FFMPEG_PID"
        kill "$FFMPEG_PID" 2>/dev/null || true
    fi
    if [[ $exit_status -eq 0 ]]; then
        rm -f "$PYTHON_OUTPUT_TEMP_FILE" "$PYTHON_LOG_TEMP_FILE"
    else
        log_message "An error occurred (Exit Code: $exit_status). Python's diagnostic output is in: $PYTHON_LOG_TEMP_FILE"
    fi
    log_message "--- Cleanup finished ---"
    exit $exit_status
}

fatal_error_dialog() {
    local error_details="$1"
    local escaped_details
    escaped_details=$(echo -n "$error_details" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g')

    yad --title="Fatal Transcription Error" \
        --text="<span color='red' size='large'><b>An Unrecoverable Error Occurred</b></span>\n\n<b>Details:</b>" \
        --width=800 --height=600 --button="Close:1" --fixed --center \
        --text-info --wrap < <(echo -e "$escaped_details\n\n--- A detailed execution log is available at: $LOG_FILE ---")
    
    log_message "FATAL: $error_details"
    exit 1
}

check_dependencies() {
    log_message "Checking for required dependencies..."
    local dependencies=("ffmpeg" "yad" "wl-copy" "pactl" "notify-send")
    for dep in "${dependencies[@]}"; do
        if ! command -v "$dep" &> /dev/null; then
            local install_hint=""
            if [[ "$dep" == "notify-send" ]]; then
                install_hint=" (Hint: On Arch Linux, install the 'libnotify' package)"
            fi
            fatal_error_dialog "Required dependency '$dep' is not installed or not in your PATH.$install_hint"
        fi
        log_message "  [✔] Found $dep"
    done
}

# --- Main Script Logic ---

trap cleanup EXIT SIGINT SIGTERM

log_message "--- Script initiated ---"

check_dependencies
log_message "Creating audio directory (if it doesn't exist): $AUDIO_DIR"
mkdir -p "$AUDIO_DIR" || fatal_error_dialog "Failed to create audio directory at '$AUDIO_DIR'. Check permissions."

log_message "Determining next audio filename..."
last_num=0
for f in "$AUDIO_DIR"/*_mic.wav; do
    [ -e "$f" ] || continue
    base_f=$(basename -- "$f")
    num_part=${base_f%%_*}
    if [[ "$num_part" =~ ^[0-9]+$ && "$num_part" -gt "$last_num" ]]; then
        last_num=$num_part
    fi
done
next_num=$((last_num + 1))
readonly AUDIO_FILE="${AUDIO_DIR}/${next_num}_mic.wav"
log_message "Next audio file will be: $AUDIO_FILE"


log_message "Determining default audio source..."
DEFAULT_SOURCE=$(pactl get-default-source)
log_message "Using audio source: $DEFAULT_SOURCE"

log_message "Starting audio recording (ffmpeg)..."
ffmpeg -y -f pulse -i "$DEFAULT_SOURCE" "$AUDIO_FILE" -loglevel error &
FFMPEG_PID=$!
log_message "ffmpeg started with PID: $FFMPEG_PID"

log_message "Displaying 'Recording' dialog..."
yad --title="Voice Transcriber" --text="<span size='large'><b>🔴 Recording...</b></span>\n\nClick <b>Stop</b> to finish recording." --width=350 --height=120 --button="Stop:0" --fixed --center
log_message "Stop button clicked. Ending recording."

log_message "Sending SIGINT to ffmpeg PID $FFMPEG_PID to finalize audio file."
kill -SIGINT "$FFMPEG_PID"
wait "$FFMPEG_PID" 2>/dev/null || true
FFMPEG_PID=""
log_message "ffmpeg process terminated."

# Removed the 'Transcribing' dialog as per user request.
log_message "Commencing transcription process via Python script..."

py_exit_code=0
# *** RE-ARCHITECTED ***: Invoke the Python interpreter from the virtual environment
# directly by its absolute path. This is the most robust method to ensure the
# correct interpreter and its associated packages are used, preventing the
# 'ModuleNotFoundError' by bypassing any ambiguity in the system's $PATH.
(
    "${VENV_PATH}bin/python3" -u "$PYTHON_SCRIPT" > "$PYTHON_OUTPUT_TEMP_FILE"
) 2> "$PYTHON_LOG_TEMP_FILE" || py_exit_code=$?

cat "$PYTHON_LOG_TEMP_FILE" >> "$LOG_FILE"
log_message "--- Python execution finished with exit code: $py_exit_code ---"

# The YAD_PID is now irrelevant as the progress dialog has been removed.
# kill "$YAD_PID" 2>/dev/null || true
# YAD_PID=""

if [[ $py_exit_code -ne 0 ]]; then
    error_content=$(<"$PYTHON_LOG_TEMP_FILE")
    notify-send --app-name="Transcriber" --icon=dialog-error "Transcription Failed" "The Python script returned an error. Check logs." --expire-time=10000 || true
    fatal_error_dialog "The Python transcription script failed with exit code $py_exit_code.\n\n$error_content"
fi

log_message "Transcription successful. Processing output."
FINAL_TEXT=$(<"$PYTHON_OUTPUT_TEMP_FILE")
log_message "Final text processed."

if [[ -n "$FINAL_TEXT" ]]; then
    log_message "Copying final text to clipboard."
    echo -n "$FINAL_TEXT" | wl-copy
    log_message "Text copied."
    notify-send --app-name="Transcriber" --icon=emblem-ok "Transcription Success" "Text copied to clipboard." --expire-time=3000 || true
else
    log_message "Warning: Transcription produced no text."
    notify-send --app-name="Transcriber" --icon=dialog-warning "Transcription Warning" "Process finished, but no text was produced." --expire-time=5000 || true
fi

log_message "--- Script finished successfully ---"


exit 0
