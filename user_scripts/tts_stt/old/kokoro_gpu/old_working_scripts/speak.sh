#!/bin/bash
set -o pipefail

# --- Configuration ---
KOKORO_APP_DIR="$HOME/contained_apps/uv/kokoro_gpu"
PYTHON_SCRIPT_PATH="$HOME/user_scripts/tts_stt/kokoro_gpu/speak.py"
SAVE_DIR="/mnt/zram1/kokoro_gpu"
MPV_PLAYBACK_SPEED="2.2"
AUDIO_RATE=24000
AUDIO_CHANNELS=1
AUDIO_FORMAT="f32le"
BUFFER_SIZE="512M" 

# --- Safety Protocol ---
# Ensures that if MPV is killed, the entire pipe is dismantled gracefully.
cleanup() {
    trap - SIGTERM && kill -- -$$ 2>/dev/null
}
trap cleanup EXIT INT TERM

# --- Checks ---
if [[ "$EUID" -eq 0 ]]; then
    notify-send "Kokoro Error" "Don't run as root." -u critical
    exit 1
fi
if ! command -v mbuffer &> /dev/null; then
    notify-send "Kokoro Error" "Please install 'mbuffer'" -u critical
    exit 1
fi

# --- Setup ---
mkdir -p "$SAVE_DIR"

# ATTEMPT 1: Try standard Clipboard (Ctrl+C). 
# We removed '--no-newline' because it causes failures with Featherpad/Terminals.
CLIPBOARD_TEXT=$(wl-paste 2>/dev/null)

# ATTEMPT 2: If Clipboard is empty, try Primary (Highlight selection).
if [[ -z "$CLIPBOARD_TEXT" ]]; then
    CLIPBOARD_TEXT=$(wl-paste --primary 2>/dev/null)
fi

if [[ -z "$CLIPBOARD_TEXT" ]]; then
    notify-send "Kokoro TTS" "Clipboard empty." -u low
    exit 0
fi

# --- Naming Logic ---
FILENAME_WORDS=$(echo "$CLIPBOARD_TEXT" | tr -s '[:space:]' '\n' | tr -cd '[:alnum:]\n' | tr '[:upper:]' '[:lower:]' | grep . | head -n 5 | paste -sd _)
[[ -z "$FILENAME_WORDS" ]] && FILENAME_WORDS="audio"
LAST_INDEX=$(find "$SAVE_DIR" -type f -name "*.wav" -print0 | xargs -0 -n 1 basename | cut -d'_' -f1 | grep '^[0-9]\+$' | sort -rn | head -n 1)
[[ -z "$LAST_INDEX" ]] && LAST_INDEX=0
NEXT_INDEX=$((LAST_INDEX + 1))
FINAL_FILENAME="${NEXT_INDEX}_${FILENAME_WORDS}.wav"
FULL_PATH="$SAVE_DIR/$FINAL_FILENAME"

notify-send "Kokoro TTS" "Streaming: '${FILENAME_WORDS//_/ }...'" -u low

# --- The Decoupled Pipeline ---
cd "$KOKORO_APP_DIR" || exit 1

# 1. Python generates raw audio at max speed.
# 2. 'tee' splits the stream. 
#    - Path A: Immediately writes to FFmpeg (Process Substitution). Since disk I/O is fast, this completes instantly.
#    - Path B: Pipes to 'mbuffer'.
# 3. 'mbuffer' absorbs the ENTIRE stream into RAM (up to 512MB), allowing 'tee' and Python to finish and exit.
# 4. 'mpv' plays from the 'mbuffer' reservoir.
#
# Result: The file is fully saved on disk moments after Python finishes, even if MPV is only at 1% playback.

echo "$CLIPBOARD_TEXT" | \
uv run python "$PYTHON_SCRIPT_PATH" | \
tee --output-error=exit >(ffmpeg -f "$AUDIO_FORMAT" -ar "$AUDIO_RATE" -ac "$AUDIO_CHANNELS" -i pipe:0 -y "$FULL_PATH" -loglevel quiet) | \
mbuffer -q -m "$BUFFER_SIZE" | \
mpv \
  --no-terminal \
  --force-window \
  --title="Kokoro TTS" \
  --x11-name=kokoro \
  --wayland-app-id=kokoro \
  --geometry=400x100 \
  --keep-open=yes \
  --speed="$MPV_PLAYBACK_SPEED" \
  --demuxer=rawaudio \
  --demuxer-rawaudio-rate="$AUDIO_RATE" \
  --demuxer-rawaudio-channels="$AUDIO_CHANNELS" \
  --demuxer-rawaudio-format=float \
  --cache=yes \
  --cache-secs=30 \
  -

exit 0
