#!/bin/bash
#
# Name: transcribe_voice
# Architecture: Arch Linux / Hyprland / uv
# Description: Records audio via parecord, processes via Python/Whisper.
# Features: Non-blocking sounds, Low Latency recording, Timestamp naming.

# --- Strict Mode ---
set -euo pipefail

# --- Configuration ---
# Set to "false" to mute all audio cues/indicator or "true" to enable them.
readonly ENABLE_SOUNDS="false"

readonly HOME_DIR="$HOME"
readonly SCRIPT_DIR="${HOME_DIR}/user_scripts/tts_stt/faster_whisper"
readonly VENV_PYTHON="${HOME_DIR}/contained_apps/uv/fasterwhisper_cpu/bin/python3"
readonly PYTHON_SCRIPT="${SCRIPT_DIR}/config.py"
readonly AUDIO_DIR="/mnt/zram1/mic"

# --- Logging Setup ---
readonly LOG_FILE="/tmp/transcribe_voice.log"
readonly PY_OUT="/tmp/transcription.output"
readonly PY_LOG="/tmp/transcription.log"
readonly REC_LOG="/tmp/recording_native.log"

# Clear logs
: > "$LOG_FILE" > "$PY_OUT" > "$PY_LOG" > "$REC_LOG"

# --- Globals ---
REC_PID=""

# --- Functions ---

log() {
    echo -e "[$(date '+%T')] $1" | tee -a "$LOG_FILE" >&2
}

play_sound() {
    # Only play if enabled
    if [[ "$ENABLE_SOUNDS" == "true" ]]; then
        # Run in background (&) so UI doesn't lag
        canberra-gtk-play -i "$1" >/dev/null 2>&1 &
    fi
}

cleanup() {
    local code=$?
    if [[ -n "$REC_PID" ]] && kill -0 "$REC_PID" 2>/dev/null; then
        log "Stopping recording process..."
        kill "$REC_PID" 2>/dev/null || true
    fi
    
    if [[ $code -ne 0 ]]; then
        play_sound "dialog-error"
        log "Script failed with exit code $code."
        if [[ -s "$REC_LOG" ]]; then
            log "Recording Log Content:"
            cat "$REC_LOG" >> "$LOG_FILE"
        fi
        notify-send -u critical "Transcription Failed" "Check logs at $LOG_FILE"
    fi
}
trap cleanup EXIT INT TERM

fatal() {
    log "FATAL: $1"
    notify-send -u critical "Error" "$1"
    exit 1
}

# --- Main Execution ---

# 1. Prereq Checks
[[ -x "$VENV_PYTHON" ]] || fatal "Python venv not found at $VENV_PYTHON"
[[ -f "$PYTHON_SCRIPT" ]] || fatal "Python script not found at $PYTHON_SCRIPT"
command -v parecord >/dev/null 2>&1 || fatal "'parecord' not found. Install 'pulseaudio-utils'."
mkdir -p "$AUDIO_DIR" || fatal "Cannot create $AUDIO_DIR"

# 2. Determine Filename (Timestamp Strategy)
readonly AUDIO_FILE="${AUDIO_DIR}/rec_$(date +%Y%m%d_%H%M%S).wav"
log "Target Audio: $AUDIO_FILE"

# 3. Record Audio (Native PulseAudio)
DEFAULT_SOURCE=$(pactl get-default-source)
log "Recording from: $DEFAULT_SOURCE"

# Audio Feedback: Start
play_sound "message-new-instant"

# --latency-msec=50 reduces buffering so data hits disk faster
parecord --device="$DEFAULT_SOURCE" \
         --channels=1 \
         --rate=16000 \
         --file-format=wav \
         --latency-msec=50 \
         "$AUDIO_FILE" > "$REC_LOG" 2>&1 &
REC_PID=$!

sleep 0.2
if ! kill -0 "$REC_PID" 2>/dev/null; then
    fatal "Recording failed to start. Check $REC_LOG."
fi

# 4. GUI Control
yad --title="Voice Transcriber" \
    --text="<span size='large' weight='bold'>🎙️ Recording...</span>\n\nWriting to ZRAM." \
    --width=300 --button="Stop Recording:0" \
    --fixed --center --on-top --undecorated

# 5. Stop Recording
log "Stopping recording..."

# "Tail Capture": Wait 0.5s to capture the last word
sleep 0.5 

kill -SIGINT "$REC_PID"
wait "$REC_PID" || true
REC_PID=""

# Audio Feedback: Processing
play_sound "button-toggle-on"

# 6. Verify Recording
if [[ ! -s "$AUDIO_FILE" ]]; then
    fatal "Audio file is empty or missing: $AUDIO_FILE"
fi

# 7. Transcribe
log "Invoking Whisper (distil-small.en)..."
notify-send -t 2000 "Transcribing..." "Processing audio..."

if ! "$VENV_PYTHON" -u "$PYTHON_SCRIPT" "$AUDIO_FILE" > "$PY_OUT" 2> "$PY_LOG"; then
    fatal "Python script failed. See $PY_LOG"
fi

# 8. Finalize
FINAL_TEXT=$(<"$PY_OUT")

if [[ -n "$FINAL_TEXT" ]]; then
    echo -n "$FINAL_TEXT" | wl-copy
    log "Success: '$FINAL_TEXT'"
    play_sound "complete"
    notify-send -t 3000 "Transcription Ready" "Copied to clipboard."
else
    log "Warning: Empty transcription."
    notify-send -u low "Transcription Empty" "No speech detected."
fi

exit 0
