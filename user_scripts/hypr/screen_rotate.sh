#!/usr/bin/env bash
# ==============================================================================
#  ARCH LINUX / HYPRLAND / UWSM — SMART ROTATION UTILITY
#  Role: Elite DevOps Automation
#  Description: Context-aware screen rotation that preserves scale factors.
# ==============================================================================

# 1. Strict Mode & Safety (Bash 5+ Standards)
# ------------------------------------------------------------------------------
set -euo pipefail
IFS=$'\n\t'

# 2. Global Constants (ANSI-C Quoting for "Elite" Color Handling)
# ------------------------------------------------------------------------------
readonly C_RED=$'\e[31m'
readonly C_GREEN=$'\e[32m'
readonly C_YELLOW=$'\e[33m'
readonly C_BLUE=$'\e[34m'
readonly C_BOLD=$'\e[1m'
readonly C_RESET=$'\e[0m'

# cleanup_trap: Ensures clean exit codes are respected.
cleanup_trap() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        printf "%s[ERROR]%s Script aborted unexpectedly (Exit Code: %d).\n" \
            "$C_RED" "$C_RESET" "$exit_code" >&2
    fi
}
trap cleanup_trap EXIT

# 3. Environment & Privilege Checks
# ------------------------------------------------------------------------------
# Dependency Check: We need 'jq' for JSON parsing.
if ! command -v jq &> /dev/null; then
    printf "%s[ERROR]%s 'jq' is missing. Install it with: sudo pacman -S jq\n" \
        "$C_RED" "$C_RESET" >&2
    exit 1
fi

# Root Check: Hyprland IPC fails if executed as root/sudo due to socket ownership.
if [[ $EUID -eq 0 ]]; then
    printf "%s[ERROR]%s Root detected. Please run this as your normal user to access the Hyprland socket.\n" \
        "$C_RED" "$C_RESET" >&2
    exit 1
fi

# 4. Argument Parsing (+90 or -90)
# ------------------------------------------------------------------------------
DIRECTION=0

if [[ $# -ne 1 ]]; then
    printf "%s[INFO]%s Usage: %s [+90|-90]\n" \
        "$C_YELLOW" "$C_RESET" "${0##*/}"
    exit 1
fi

case "$1" in
    "+90") DIRECTION=1 ;;  # Clockwise
    "-90") DIRECTION=-1 ;; # Counter-Clockwise
    *) 
        printf "%s[ERROR]%s Invalid flag '%s'. Use +90 or -90.\n" \
            "$C_RED" "$C_RESET" "$1" >&2
        exit 1 
        ;;
esac

# 5. Hardware Detection (Smart Query)
# ------------------------------------------------------------------------------
# Fetch all monitors to handle multi-monitor setups
MON_STATE=$(hyprctl monitors -j)

# Count number of monitors
MON_COUNT=$(printf "%s" "$MON_STATE" | jq 'length')

# Validation: Ensure we actually found monitors
if [[ "$MON_COUNT" -eq 0 ]]; then
    printf "%s[ERROR]%s No active monitors detected via Hyprland IPC.\n" \
        "$C_RED" "$C_RESET" >&2
    exit 1
fi

# 6. Detect Active Monitor (where mouse cursor is)
# ------------------------------------------------------------------------------
# Get cursor position
CURSOR_INFO=$(hyprctl cursorpos)
CURSOR_X=$(echo "$CURSOR_INFO" | awk '{print $1}' | tr -d ',')
CURSOR_Y=$(echo "$CURSOR_INFO" | awk '{print $2}')

printf "%s[INFO]%s Cursor position: %d, %d\n" \
    "$C_BLUE" "$C_RESET" "$CURSOR_X" "$CURSOR_Y"

# Find which monitor contains the cursor
ACTIVE_MONITOR=""
for i in $(seq 0 $((MON_COUNT - 1))); do
    MON_NAME=$(printf "%s" "$MON_STATE" | jq -r ".[$i].name")
    MON_X=$(printf "%s" "$MON_STATE" | jq -r ".[$i].x")
    MON_Y=$(printf "%s" "$MON_STATE" | jq -r ".[$i].y")
    MON_WIDTH=$(printf "%s" "$MON_STATE" | jq -r ".[$i].width")
    MON_HEIGHT=$(printf "%s" "$MON_STATE" | jq -r ".[$i].height")

    # Check if cursor is within this monitor's bounds
    if [[ $CURSOR_X -ge $MON_X ]] && [[ $CURSOR_X -lt $((MON_X + MON_WIDTH)) ]] && \
       [[ $CURSOR_Y -ge $MON_Y ]] && [[ $CURSOR_Y -lt $((MON_Y + MON_HEIGHT)) ]]; then
        ACTIVE_MONITOR="$i"
        printf "%s[INFO]%s Detected active monitor: %s%s%s\n" \
            "$C_BLUE" "$C_RESET" "$C_BOLD" "$MON_NAME" "$C_RESET"
        break
    fi
done

# Fallback to first monitor if detection fails
if [[ -z "$ACTIVE_MONITOR" ]]; then
    ACTIVE_MONITOR="0"
    printf "%s[WARNING]%s Could not detect cursor monitor, using first monitor.\n" \
        "$C_YELLOW" "$C_RESET"
fi

# 7. Rotate Only the Active Monitor
# ------------------------------------------------------------------------------
# Extract monitor details for the active monitor
NAME=$(printf "%s" "$MON_STATE" | jq -r ".[$ACTIVE_MONITOR].name")
SCALE=$(printf "%s" "$MON_STATE" | jq -r ".[$ACTIVE_MONITOR].scale")
CURRENT_TRANSFORM=$(printf "%s" "$MON_STATE" | jq -r ".[$ACTIVE_MONITOR].transform")
WIDTH=$(printf "%s" "$MON_STATE" | jq -r ".[$ACTIVE_MONITOR].width")
HEIGHT=$(printf "%s" "$MON_STATE" | jq -r ".[$ACTIVE_MONITOR].height")
REFRESH=$(printf "%s" "$MON_STATE" | jq -r ".[$ACTIVE_MONITOR].refreshRate")
POS_X=$(printf "%s" "$MON_STATE" | jq -r ".[$ACTIVE_MONITOR].x")
POS_Y=$(printf "%s" "$MON_STATE" | jq -r ".[$ACTIVE_MONITOR].y")

# Calculate new transform using modulo arithmetic
# Hyprland Transforms: 0=Normal, 1=90, 2=180, 3=270
NEW_TRANSFORM=$(( (CURRENT_TRANSFORM + DIRECTION + 4) % 4 ))

printf "%s[INFO]%s Rotating %s%s%s (Scale: %s): %d -> %d\n" \
    "$C_BLUE" "$C_RESET" "$C_BOLD" "$NAME" "$C_RESET" "$SCALE" "$CURRENT_TRANSFORM" "$NEW_TRANSFORM"

# Apply rotation while preserving position
# Use exact resolution and position to maintain layout
if hyprctl keyword monitor "${NAME}, ${WIDTH}x${HEIGHT}@${REFRESH}, ${POS_X}x${POS_Y}, ${SCALE}, transform, ${NEW_TRANSFORM}" > /dev/null; then
    printf "%s[SUCCESS]%s Rotation applied for %s.\n" \
        "$C_GREEN" "$C_RESET" "$NAME"

    # Notify user visually if notify-send is available
    if command -v notify-send &> /dev/null; then
        notify-send -a "System" "Display Rotated" "Monitor: $NAME\nTransform: $NEW_TRANSFORM" -h string:x-canonical-private-synchronous:display-rotate
    fi
else
    printf "%s[ERROR]%s Failed to apply rotation for %s.\n" \
        "$C_RED" "$C_RESET" "$NAME" >&2
    exit 1
fi

# Clean exit
trap - EXIT
exit 0
