#!/usr/bin/env bash
#
# volume-slider - A simple GUI volume control using yad and WirePlumber
#

set -euo pipefail

# ==============================================================================
# CONFIGURATION
# ==============================================================================
readonly APP_NAME="volume-slider"
readonly TITLE="Volume"
readonly LOCK_FILE="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/${APP_NAME}.lock"

# ==============================================================================
# UTILITY FUNCTIONS
# ==============================================================================
die() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

show_usage() {
    cat <<EOF
Usage: ${0##*/} [OPTIONS]

A simple volume slider using yad and WirePlumber.

Options:
    -s SINK    Audio sink to control (default: @DEFAULT_AUDIO_SINK@)
    -h         Show this help message
EOF
}

# ==============================================================================
# SINGLE INSTANCE GUARD
# ==============================================================================
focus_existing_window() {
    local addr=""
    
    # Try Hyprland first
    if command -v hyprctl &>/dev/null; then
        if command -v jq &>/dev/null; then
            addr=$(hyprctl clients -j 2>/dev/null | \
                   jq -r --arg c "$APP_NAME" \
                      '.[] | select(.class == $c) | .address' 2>/dev/null | \
                   head -n1) || true
                   
            if [[ -n "$addr" && "$addr" != "null" ]]; then
                hyprctl dispatch focuswindow "address:$addr" &>/dev/null || true
                return 0
            fi
        fi
        # Fallback: focus by title pattern
        hyprctl dispatch focuswindow "title:^${TITLE}$" &>/dev/null || true
        return 0
    fi
    
    # Try wmctrl for X11/other WMs
    if command -v wmctrl &>/dev/null; then
        wmctrl -a "$TITLE" &>/dev/null || true
    fi
}

# Ensure lock directory exists
mkdir -p "${LOCK_FILE%/*}" 2>/dev/null || true

# Open file descriptor for locking
exec 200>"$LOCK_FILE" || die "Cannot create lock file: $LOCK_FILE"

# Try to acquire exclusive lock (non-blocking)
if ! flock -n 200; then
    focus_existing_window
    exit 0
fi

# ==============================================================================
# ARGUMENT PARSING
# ==============================================================================
SINK="@DEFAULT_AUDIO_SINK@"

while getopts ":s:h" opt; do
    case "$opt" in
        s) SINK="$OPTARG" ;;
        h) show_usage; exit 0 ;;
        :) die "Option -${OPTARG} requires an argument" ;;
        ?) die "Unknown option: -${OPTARG}" ;;
    esac
done
shift $((OPTIND - 1))

# ==============================================================================
# DEPENDENCY CHECKS
# ==============================================================================
command -v yad &>/dev/null   || die "yad is required but not installed"
command -v wpctl &>/dev/null || die "wpctl (WirePlumber) is required but not installed"

# ==============================================================================
# AUDIO BACKEND
# ==============================================================================
get_volume() {
    local output
    output=$(wpctl get-volume "$SINK" 2>&1) || return 1
    # Parse: "Volume: 0.75" or "Volume: 0.75 [MUTED]"
    awk '{printf "%d", $2 * 100}' <<< "$output"
}

set_volume() {
    local vol="$1"
    
    # Validate input is a non-negative integer
    [[ "$vol" =~ ^[0-9]+$ ]] || return 1
    
    # Clamp to valid range (0-100)
    ((vol = vol > 100 ? 100 : vol))
    
    # Set the volume
    wpctl set-volume "$SINK" "${vol}%" 2>/dev/null || return 1
    
    # Auto-unmute when volume > 0
    ((vol > 0)) && wpctl set-mute "$SINK" 0 &>/dev/null
    
    return 0
}

# ==============================================================================
# INITIALIZATION
# ==============================================================================
CURRENT_VOL=$(get_volume) || die "Cannot get volume for sink: $SINK"

# Validate retrieved volume; fallback to 50%
[[ "$CURRENT_VOL" =~ ^[0-9]+$ ]] || CURRENT_VOL=50

# ==============================================================================
# YAD UI CONFIGURATION
# ==============================================================================
YAD_ARGS=(
    --scale
    --title="$TITLE"
    --window-icon=audio-volume-medium
    --class="$APP_NAME"
    --text=""
    --text-align=center
    --min-value=0
    --max-value=100
    --value="$CURRENT_VOL"
    --step=1
    --show-value
    --print-partial
    --width=420
    --height=90
    --fixed
    --buttons-layout=center
    --button="Close":1
)

# ==============================================================================
# MAIN EVENT LOOP
# ==============================================================================
last_vol="$CURRENT_VOL"

# Disable errexit for UI loop (yad returns 1 on "Close" button)
set +e

# Process substitution avoids subshell variable scope issues
while IFS= read -r value; do
    # Strip any decimal portion (yad may output floats)
    value="${value%%.*}"
    
    # Skip empty or invalid values
    [[ "$value" =~ ^[0-9]+$ ]] || continue
    
    # Update volume only if changed
    if ((value != last_vol)); then
        set_volume "$value" && last_vol="$value"
    fi
done < <(yad "${YAD_ARGS[@]}" 2>/dev/null)

# Always exit cleanly
exit 0
