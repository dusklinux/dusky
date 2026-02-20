#!/usr/bin/env bash
#
# brightness-slider - A GTK brightness control slider
# Dependencies: yad, brightnessctl
# Optional: hyprctl+jq (Hyprland), wmctrl (X11)
#

set -euo pipefail

# --- CONFIGURATION ---
readonly APP_NAME="brightness-slider"
readonly TITLE="Brightness"
readonly LOCK_FILE="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/${APP_NAME}.lock"

# --- SINGLE INSTANCE GUARD (FLOCK) ---
exec 200>"$LOCK_FILE"

focus_existing_window() {
    local addr=""

    # Try Hyprland/hyprctl first
    if command -v hyprctl >/dev/null 2>&1; then
        if command -v jq >/dev/null 2>&1; then
            addr=$(
                hyprctl clients -j 2>/dev/null \
                | jq -r --arg c "$APP_NAME" \
                    '.[] | select(.class == $c) | .address' \
                | head -n1
            ) || true

            if [[ -n "$addr" && "$addr" != "null" ]]; then
                hyprctl dispatch focuswindow "address:$addr" >/dev/null 2>&1 || true
                return 0
            fi
        fi
        # Fallback: match by title regex
        hyprctl dispatch focuswindow "title:^${TITLE}$" >/dev/null 2>&1 || true
        return 0
    fi

    # Fallback: wmctrl for X11/other WMs
    if command -v wmctrl >/dev/null 2>&1; then
        wmctrl -a "$TITLE" 2>/dev/null || true
    fi
}

if ! flock -n 200; then
    focus_existing_window
    exit 0
fi

# --- ARGUMENT PARSING ---
device=""
class=""

show_help() {
    cat <<EOF
Usage: ${0##*/} [OPTIONS]

Options:
    -d DEVICE    Specify the backlight device
    -c CLASS     Specify the device class  
    -h           Show this help message

Examples:
    ${0##*/} -d intel_backlight
    ${0##*/} -c backlight
EOF
}

while getopts ":d:c:h" opt; do
    case "$opt" in
        d) device="$OPTARG" ;;
        c) class="$OPTARG" ;;
        h) show_help; exit 0 ;;
        :) echo "Error: Option -$OPTARG requires an argument" >&2; exit 1 ;;
        \?) echo "Error: Unknown option: -$OPTARG" >&2; exit 1 ;;
    esac
done
shift $((OPTIND - 1))

# Build brightnessctl command array
brightnessctl_cmd=(brightnessctl)
[[ -n "$device" ]] && brightnessctl_cmd+=(--device="$device")
[[ -n "$class" ]] && brightnessctl_cmd+=(--class="$class")

# --- DEPENDENCY CHECKS ---
missing_deps=()
command -v yad >/dev/null 2>&1 || missing_deps+=(yad)
command -v brightnessctl >/dev/null 2>&1 || missing_deps+=(brightnessctl)

if ((${#missing_deps[@]} > 0)); then
    echo "Error: Missing required dependencies: ${missing_deps[*]}" >&2
    exit 1
fi

# --- HELPER FUNCTIONS ---
get_brightness_percent() {
    local current max pct

    current=$("${brightnessctl_cmd[@]}" get 2>/dev/null) || { echo 50; return; }
    max=$("${brightnessctl_cmd[@]}" max 2>/dev/null) || { echo 50; return; }

    # Validate: must be non-negative integers
    if ! [[ "$current" =~ ^[0-9]+$ && "$max" =~ ^[0-9]+$ ]]; then
        echo 50
        return
    fi

    # Prevent division by zero
    if ((max == 0)); then
        echo 50
        return
    fi

    # Calculate percentage with proper rounding
    pct=$(( (current * 100 + max / 2) / max ))

    # Clamp to valid range
    ((pct < 1)) && pct=1
    ((pct > 100)) && pct=100

    echo "$pct"
}

set_brightness() {
    local value="$1"
    "${brightnessctl_cmd[@]}" set "${value}%" >/dev/null 2>&1 || true
}

# --- MAIN EXECUTION ---
current_pct=$(get_brightness_percent)

yad_args=(
    --scale
    --title="$TITLE"
    --text="󰃠"
    --text-align=center
    --class="$APP_NAME"
    --window-icon="video-display"
    --min-value=1
    --max-value=100
    --value="$current_pct"
    --step=1
    --print-partial
    --width=420
    --height=90
    --buttons-layout=center
    --button="Close:1"
    --fixed
)

# Process slider changes in real-time
# IMPORTANT: Using process substitution (<(...)) instead of pipe
# to avoid subshell scoping issues - variable updates persist!
while IFS= read -r value; do
    # Remove any decimal portion (yad may output floats)
    value_int="${value%.*}"

    # Validate: non-empty, numeric, in range, and actually changed
    if [[ -n "$value_int" && "$value_int" =~ ^[0-9]+$ ]]; then
        if ((value_int != current_pct && value_int >= 1 && value_int <= 100)); then
            set_brightness "$value_int"
            current_pct="$value_int"
        fi
    fi
done < <(yad "${yad_args[@]}" 2>/dev/null || true)

exit 0
