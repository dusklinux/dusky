#!/bin/bash
# -----------------------------------------------------------------------------
# hypr_songrec.sh - Shazam-like audio recognition for Hyprland/Wayland
# -----------------------------------------------------------------------------

set -euo pipefail

# -----------------------------------------------------------------------------
# Constants & Configuration
# -----------------------------------------------------------------------------
readonly SCRIPT_NAME="${0##*/}"
readonly TIMEOUT_SECS=30
readonly RECORD_SECS=5
readonly LOCK_FILE="/tmp/hypr_songrec.lock"
readonly LOG_FILE="/tmp/hypr_songrec.log"

declare -Ar PACMAN_DEPS=(
    ["ffmpeg"]="ffmpeg"
    ["notify-send"]="libnotify"
    ["jq"]="jq"
    ["pactl"]="libpulse"
    ["parec"]="libpulse"
    ["songrec"]="songrec"
)

TMP_DIR=""
RAW_FILE=""
MP3_FILE=""
PAREC_PID=""

# -----------------------------------------------------------------------------
# Utility Functions
# -----------------------------------------------------------------------------
log_info() {
    printf '[%s] INFO: %s\n' "$SCRIPT_NAME" "$1" >> "$LOG_FILE"
}

log_error() {
    printf '[%s] ERROR: %s\n' "$SCRIPT_NAME" "$1" >> "$LOG_FILE"
    [[ -t 2 ]] && printf '[%s] ERROR: %s\n' "$SCRIPT_NAME" "$1" >&2
}

die() {
    log_error "$1"
    exit "${2:-1}"
}

# -----------------------------------------------------------------------------
# 0. Auto-Install Dependencies
# -----------------------------------------------------------------------------
install_dependencies() {
    local -a to_install=()
    local cmd

    for cmd in "${!PACMAN_DEPS[@]}"; do
        if ! command -v "$cmd" &>/dev/null; then
            to_install+=("${PACMAN_DEPS[$cmd]}")
        fi
    done

    if (( ${#to_install[@]} > 0 )); then
        if ! sudo -n true 2>/dev/null; then
            die "Missing dependencies (${to_install[*]}) and sudo requires a password. Install manually: sudo pacman -S ${to_install[*]}"
        fi
        log_info "Installing missing dependencies: ${to_install[*]}"
        if sudo pacman -S --needed "${to_install[@]}"; then
            log_info "Dependencies installed successfully."
        else
            die "Failed to install dependencies via pacman."
        fi
    fi
    return 0
}

# -----------------------------------------------------------------------------
# 1. Singleton Lock (Atomic using flock)
# -----------------------------------------------------------------------------
acquire_lock() {
    exec 200>"$LOCK_FILE"
    if ! flock -n 200; then
        exit 0
    fi
    printf '%d\n' "$$" >&200
}

# -----------------------------------------------------------------------------
# 2. Setup & Cleanup Trap
# -----------------------------------------------------------------------------
setup_environment() {
    log_info "Starting new recognition session"
    TMP_DIR=$(mktemp -d "/tmp/hypr_songrec.XXXXXX")
    RAW_FILE="${TMP_DIR}/recording.raw"
    MP3_FILE="${TMP_DIR}/recording.mp3"
}

cleanup() {
    local exit_code=$?
    set +e
    [[ -n "${PAREC_PID:-}" ]] && kill "$PAREC_PID" 2>/dev/null
    [[ -d "${TMP_DIR:-}" ]] && rm -rf "$TMP_DIR"
    log_info "Session ended with exit code $exit_code"
    exit "$exit_code"
}

# -----------------------------------------------------------------------------
# 3. Audio Functions
# -----------------------------------------------------------------------------
get_monitor_source() {
    local default_sink

    if ! default_sink=$(pactl get-default-sink 2>>"$LOG_FILE") || [[ -z "$default_sink" ]]; then
        die "Failed to get default audio sink from pactl."
    fi

    printf '%s.monitor' "$default_sink"
}

record_clip() {
    local monitor_source="$1"

    timeout "$RECORD_SECS" parec -d "$monitor_source" \
        --format=s16le --rate=44100 --channels=2 \
        > "$RAW_FILE" 2>>"$LOG_FILE" &
    PAREC_PID=$!

    for (( i=1; i<=RECORD_SECS; i++ )); do
        printf '\r  ⏺ Recording... %d/%ds' "$i" "$RECORD_SECS"
        sleep 1
    done
    printf '\n'

    wait "$PAREC_PID" 2>/dev/null || true
    PAREC_PID=""

    if [[ ! -s "$RAW_FILE" ]]; then
        log_error "Raw recording is empty."
        return 1
    fi

    if ! ffmpeg -f s16le -ar 44100 -ac 2 -i "$RAW_FILE" \
        -vn -acodec libmp3lame -q:a 2 -y -loglevel error \
        "$MP3_FILE" 2>>"$LOG_FILE"; then
        log_error "FFmpeg failed to convert raw audio to MP3."
        return 1
    fi

    if [[ ! -s "$MP3_FILE" ]]; then
        log_error "Converted MP3 file is empty."
        return 1
    fi

    return 0
}

recognize_song() {
    local json

    if ! json=$(songrec recognize --json "$MP3_FILE" 2>>"$LOG_FILE"); then
        log_info "songrec returned non-zero."
        return 1
    fi

    [[ -z "$json" ]] && return 1

    local parsed
    if ! parsed=$(printf '%s' "$json" | jq -re '.track | [.title, .subtitle] | @tsv' 2>>"$LOG_FILE"); then
        return 1
    fi

    local title artist
    IFS=$'\t' read -r title artist <<< "$parsed"

    [[ -z "$title" ]] && return 1

    notify-send -u normal -t 10000 \
        -h string:x-canonical-private-synchronous:songrec \
        "Song Detected" "<b>${title}</b>\n${artist}"

    printf '\n  🎵  %s — %s\n\n' "$title" "$artist"

    log_info "Successfully identified: $title by $artist"
    return 0
}

# -----------------------------------------------------------------------------
# 4. Main Recognition Loop
# -----------------------------------------------------------------------------
recognition_loop() {
    local monitor_source="$1"
    local start_time=$EPOCHSECONDS
    local attempt=0

    while (( EPOCHSECONDS - start_time < TIMEOUT_SECS )); do
        (( ++attempt ))
        log_info "Recording ${RECORD_SECS}s clip... (attempt $attempt, elapsed: $(( EPOCHSECONDS - start_time ))s)"

        if record_clip "$monitor_source" && recognize_song; then
            return 0
        fi

        log_info "No match yet, retrying..."
        printf '  No match, retrying... (attempt %d, %ds elapsed)\n' "$attempt" "$(( EPOCHSECONDS - start_time ))"
    done

    notify-send -u low -t 3000 \
        -h string:x-canonical-private-synchronous:songrec \
        "SongRec" "No match found."
    printf 'No match found after %ds.\n' "$(( EPOCHSECONDS - start_time ))"
    return 1
}

# -----------------------------------------------------------------------------
# Main Entry Point
# -----------------------------------------------------------------------------
main() {
    acquire_lock

    > "$LOG_FILE"

    trap cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    trap 'exit 129' HUP

    install_dependencies
    setup_environment

    local monitor_source
    monitor_source=$(get_monitor_source)

    notify-send -u low -t 3000 \
        -h string:x-canonical-private-synchronous:songrec \
        "SongRec" "Listening..."
    printf 'Listening...\n'

    recognition_loop "$monitor_source"
}

main "$@"
