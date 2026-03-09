#!/usr/bin/env bash

# Exit immediately if a command exits with a non-zero status.
set -e
# Treat unset variables as an error when substituting.
set -u
# Causes a pipeline to return the exit status of the last command in the pipe that failed.
set -o pipefail

# --- Configuration ---
# Directory where the active Piper model is expected to be.
ACTIVE_MODEL_DIR="${HOME}/piper/models/active_model"

# Path to the Piper executable.
PIPER_EXECUTABLE="${HOME}/piper/piper/piper"

# Directory where the audio output will be saved.
OUTPUT_DIR="/mnt/zram1/PiperTTS_Audio"

# Audio player command.
AUDIO_PLAYER="mpv"

# Desired playback speed (e.g., 1.0, 1.3, 1.5, 2.0).
PLAYBACK_SPEED="2.3" # Default to 1.3x speed

# Maximum length for the text-derived part of the filename.
MAX_FILENAME_TEXT_LENGTH=60 # Adjust as needed

# Default sample rate if not found in model's JSON config.
# 22050 Hz is common for many Piper models.
DEFAULT_SAMPLE_RATE="22050"

# --- Dynamic Model Detection & Sanity Checks ---

# 1. Check if the active model directory exists
if [ ! -d "$ACTIVE_MODEL_DIR" ]; then
    echo "Error: Active model directory not found at: $ACTIVE_MODEL_DIR" >&2
    echo "Please create this directory and place your desired .onnx model file and its .json config in it." >&2
    exit 1
fi

# 2. Find .onnx model file in the active model directory
shopt -s nullglob
models_found=("$ACTIVE_MODEL_DIR"/*.onnx)
shopt -u nullglob

PIPER_MODEL=""
PIPER_MODEL_CONFIG_JSON="" # For storing path to model's .json config

if [ ${#models_found[@]} -eq 0 ]; then
    echo "Error: No .onnx model file found in $ACTIVE_MODEL_DIR" >&2
    exit 1
elif [ ${#models_found[@]} -gt 1 ]; then
    echo "Warning: Multiple .onnx models found in $ACTIVE_MODEL_DIR." >&2
    PIPER_MODEL="${models_found[0]}"
    echo "Using the first one found: $PIPER_MODEL" >&2
else
    PIPER_MODEL="${models_found[0]}"
    echo "Using Piper model: $PIPER_MODEL"
fi

# Attempt to find the corresponding .json config file (same name, different extension)
PIPER_MODEL_CONFIG_JSON="${PIPER_MODEL%.onnx}.json"


# 3. Check if wl-paste is installed
if ! command -v wl-paste &> /dev/null; then
    echo "Error: wl-paste command not found. Please install wl-clipboard." >&2
    exit 1
fi

# 4. Check if the Piper executable exists
if [ ! -x "$PIPER_EXECUTABLE" ]; then
    echo "Error: Piper executable not found or not executable at: $PIPER_EXECUTABLE" >&2
    exit 1
fi

# 5. Check if the Piper model file exists
if [ ! -f "$PIPER_MODEL" ]; then
    echo "Error: Piper model file '$PIPER_MODEL' not found or is not a file." >&2
    exit 1
fi

# 6. Check if the audio player is installed
if ! command -v "$AUDIO_PLAYER" &> /dev/null; then
    echo "Error: Audio player '$AUDIO_PLAYER' not found. Please install it." >&2
    exit 1
fi

# 7. Check if ffmpeg is installed (NEW CHECK)
if ! command -v ffmpeg &> /dev/null; then
    echo "Error: ffmpeg command not found. Please install ffmpeg (e.g., sudo pacman -S ffmpeg)." >&2
    exit 1
fi

# 8. Check for jq and attempt to determine sample rate
JQ_PATH=$(command -v jq)
DETERMINED_SAMPLE_RATE=""
ACTUAL_SAMPLE_RATE_FOR_MPV="$DEFAULT_SAMPLE_RATE" # Initialize with default

if [ -f "$PIPER_MODEL_CONFIG_JSON" ]; then
    echo "Found model config file: $PIPER_MODEL_CONFIG_JSON"
    if [ -n "$JQ_PATH" ]; then
        echo "Attempting to read sample rate with jq..."
        DETERMINED_SAMPLE_RATE=$(jq -r '.audio.sample_rate // empty' "$PIPER_MODEL_CONFIG_JSON" 2>/dev/null || true)

        if [ -n "$DETERMINED_SAMPLE_RATE" ] && [[ "$DETERMINED_SAMPLE_RATE" =~ ^[0-9]+$ ]]; then
            echo "Successfully determined sample rate from config: $DETERMINED_SAMPLE_RATE Hz."
            ACTUAL_SAMPLE_RATE_FOR_MPV="$DETERMINED_SAMPLE_RATE"
        else
            echo "Warning: Could not extract a valid sample rate from $PIPER_MODEL_CONFIG_JSON (value found: '$DETERMINED_SAMPLE_RATE')." >&2
            echo "Using default sample rate for mpv and ffmpeg: $DEFAULT_SAMPLE_RATE Hz." >&2
        fi
    else
        echo "Warning: jq command not found. Cannot automatically determine audio sample rate from model config." >&2
        echo "For potentially more accurate playback and recording, install jq (e.g., sudo pacman -S jq)." >&2
        echo "Using default sample rate for mpv and ffmpeg: $DEFAULT_SAMPLE_RATE Hz." >&2
    fi
else
    echo "Warning: Model config file '$PIPER_MODEL_CONFIG_JSON' not found alongside the .onnx model." >&2
    echo "Using default sample rate for mpv and ffmpeg: $DEFAULT_SAMPLE_RATE Hz." >&2
fi


# --- Script Logic ---

# 1. Create the output directory if it doesn't exist
mkdir -p "$OUTPUT_DIR"
echo "Audio output directory: $OUTPUT_DIR"

# 2. Get text from the clipboard
echo "Fetching text from clipboard..."
INPUT_TEXT=$(wl-paste --no-newline)

if [ -z "$INPUT_TEXT" ]; then
    echo "Clipboard is empty. Nothing to synthesize." >&2
    exit 0 # Exit gracefully, not an error
fi
echo "Text to synthesize: \"$INPUT_TEXT\""

# --- Generate dynamic output filename ---
BASE_FILENAME_FROM_TEXT=$(echo "$INPUT_TEXT" | tr '\n' ' ' | awk '{for(i=1;i<=NF && i<=5;i++) printf "%s%s", $i, (i<NF && i<5 ? "_" : "")}' | tr -dc '[:alnum:]_-')
BASE_FILENAME_FROM_TEXT="${BASE_FILENAME_FROM_TEXT:0:$MAX_FILENAME_TEXT_LENGTH}" # Truncate

if [ -z "$BASE_FILENAME_FROM_TEXT" ]; then
    BASE_FILENAME_FROM_TEXT="piper_audio_$(date +%Y%m%d%H%M%S)"
fi

LAST_NUMBER=0
shopt -s nullglob
existing_files=("$OUTPUT_DIR"/*.wav)
shopt -u nullglob

for f in "${existing_files[@]}"; do
    if [[ -f "$f" ]]; then
        filename_only=$(basename "$f")
        if [[ "$filename_only" =~ ^([0-9]+)_ ]]; then
            num="${BASH_REMATCH[1]}"
            num_int=$((10#$num)) # Ensure base 10 interpretation
            if (( num_int > LAST_NUMBER )); then
                LAST_NUMBER=$num_int
            fi
        fi
    fi
done
NEXT_NUMBER=$((LAST_NUMBER + 1))
FORMATTED_NEXT_NUMBER=$NEXT_NUMBER # No leading zeros needed for this scheme

PROPOSED_FILENAME_TEXT_PART="${BASE_FILENAME_FROM_TEXT}"
PROPOSED_FILENAME="${FORMATTED_NEXT_NUMBER}_${PROPOSED_FILENAME_TEXT_PART}.wav"
PROPOSED_OUTPUT_FILE="${OUTPUT_DIR}/${PROPOSED_FILENAME}"

ACTUAL_OUTPUT_FILE="$PROPOSED_OUTPUT_FILE"
ACTUAL_FILENAME="$PROPOSED_FILENAME"
SUB_COUNTER=1
while [ -f "$ACTUAL_OUTPUT_FILE" ]; do
    ACTUAL_FILENAME="${FORMATTED_NEXT_NUMBER}_${PROPOSED_FILENAME_TEXT_PART}_v${SUB_COUNTER}.wav"
    ACTUAL_OUTPUT_FILE="${OUTPUT_DIR}/${ACTUAL_FILENAME}"
    SUB_COUNTER=$((SUB_COUNTER + 1))
done

OUTPUT_FILE="$ACTUAL_OUTPUT_FILE"
OUTPUT_FILENAME="$ACTUAL_FILENAME"

echo "Output WAV file will be: $OUTPUT_FILENAME"

# --- Prepare Piper Command Arguments ---
PIPER_CMD_ARGS=("--model" "$PIPER_MODEL")
if [ -f "$PIPER_MODEL_CONFIG_JSON" ]; then
    PIPER_CMD_ARGS+=("--config" "$PIPER_MODEL_CONFIG_JSON")
    echo "Piper will use model config: $PIPER_MODEL_CONFIG_JSON"
else
    echo "Piper will run without a specific model config file (as $PIPER_MODEL_CONFIG_JSON was not found)."
fi
PIPER_CMD_ARGS+=("--output-raw") # Piper still outputs raw, ffmpeg will handle WAV conversion

# --- Prepare MPV Options for playing raw audio stream with GUI ---
MPV_OPTS=(
    "--no-config"                     # Ignore user's global mpv config
    "--force-window=yes"              # Explicitly request a playback window (GUI)
    "--demuxer=rawaudio"              # Tell mpv it's receiving raw audio
    "--demuxer-rawaudio-format=s16le" # Piper outputs 16-bit signed little-endian PCM
    "--demuxer-rawaudio-channels=1"   # Piper outputs mono audio
    "--demuxer-rawaudio-rate=${ACTUAL_SAMPLE_RATE_FOR_MPV}" # Crucial: sample rate
    "--speed=${PLAYBACK_SPEED}"       # Apply desired playback speed
    # "--title=Piper TTS Output (Live)" # Optional: set a title for the mpv window
    # "--really-quiet"                # Uncomment to suppress mpv's own console messages for cleaner script output
)

echo "Attempting real-time playback with GUI. Sample rate for live playback: ${ACTUAL_SAMPLE_RATE_FOR_MPV} Hz at ${PLAYBACK_SPEED}x speed."
echo "Using mpv options for live playback: ${MPV_OPTS[*]}"
echo "Starting Piper, saving to WAV file via ffmpeg, and playing audio in real-time with GUI..."

# --- Synthesize, Save as WAV, and Play in Real-time with GUI (MODIFIED PIPELINE) ---
# The pipeline:
# 1. echo input text to Piper
# 2. Piper generates raw audio to stdout
# 3. tee duplicates the raw audio:
#    a) One copy is piped to ffmpeg (via process substitution) to be converted and saved as a proper WAV file.
#       ffmpeg reads raw PCM from its stdin, converts it, and writes to OUTPUT_FILE.
#       -y: overwrite output without asking
#       -f s16le: input format is signed 16-bit little-endian PCM
#       -ar: audio rate (sample rate)
#       -ac: audio channels (1 for mono)
#       -i - : input from stdin
#    b) The other copy is piped to mpv's stdin for live playback.
# 4. mpv reads raw audio from tee's stdout via pipe and plays it with GUI (the final '-' tells mpv to read from stdin)

if echo "$INPUT_TEXT" | "$PIPER_EXECUTABLE" "${PIPER_CMD_ARGS[@]}" | \
   tee >(ffmpeg -y -f s16le -ar "${ACTUAL_SAMPLE_RATE_FOR_MPV}" -ac 1 -i - "${OUTPUT_FILE}") | \
   "$AUDIO_PLAYER" "${MPV_OPTS[@]}" - ; then
    echo "Audio processing, saving to WAV, and playback finished successfully."
else
    # This block will be executed if any command in the pipeline fails.
    echo "Error or interruption during audio processing/saving/playback pipeline." >&2
    # Check if the output file was created and has content.
    # If ffmpeg failed, the file might be incomplete or not a valid WAV.
    if [ -f "$OUTPUT_FILE" ] && [ -s "$OUTPUT_FILE" ]; then
        echo "An audio file may have been created or partially created: $OUTPUT_FILE" >&2
        echo "Please check its integrity, especially if an error occurred during ffmpeg processing." >&2
    elif [ -f "$OUTPUT_FILE" ]; then
        echo "An empty audio file might have been created: $OUTPUT_FILE" >&2
    else
        echo "No audio file was saved." >&2
    fi
    echo "Pipeline ended. If mpv was closed manually, this is normal for the playback part."
fi

# Final check if the output file was created and is not empty
if [ -f "$OUTPUT_FILE" ] && [ -s "$OUTPUT_FILE" ]; then
    # A more robust check would involve ffprobe or soxi to verify WAV integrity,
    # but for simplicity, we'll assume if ffmpeg didn't cause the pipeline to fail hard,
    # and the file exists and is non-empty, it's likely okay.
    echo "Audio successfully saved as WAV file: $OUTPUT_FILE"
else
    echo "Warning: Output WAV file $OUTPUT_FILE was not created or is empty. This can happen if Piper, tee, or ffmpeg failed very early." >&2
fi

echo "Script finished."

