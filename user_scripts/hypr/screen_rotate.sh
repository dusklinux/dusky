#!/usr/bin/env bash
# ==============================================================================
#  ARCH LINUX / HYPRLAND / UWSM — SURGICAL ROTATION UTILITY (v4)
#  Description: Context-aware rotation utilizing Hybrid State-Config parsing.
#  Guarantees zero-drift for complex modelines (VRR, bitdepth, custom Hz).
# ==============================================================================

# 1. Strict Mode & Safety
# ------------------------------------------------------------------------------
set -euo pipefail
IFS=$'\n\t'

readonly C_RED=$'\e[31m'
readonly C_GREEN=$'\e[32m'
readonly C_YELLOW=$'\e[33m'
readonly C_BLUE=$'\e[34m'
readonly C_BOLD=$'\e[1m'
readonly C_RESET=$'\e[0m'

# 2. Config Paths (Strictly ordered by priority for modular setups)
# ------------------------------------------------------------------------------
readonly CONFIG_FILES=(
    "${XDG_CONFIG_HOME:-$HOME/.config}/hypr/edit_here/source/monitors.conf"
    "${XDG_CONFIG_HOME:-$HOME/.config}/hypr/source/monitors.conf"
)

# 3. Exit Handling
# ------------------------------------------------------------------------------
cleanup_trap() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        printf '%s[ERROR]%s Script aborted unexpectedly (Exit Code: %d).\n' \
            "$C_RED" "$C_RESET" "$exit_code" >&2
    fi
}
trap cleanup_trap EXIT

die() {
    trap - EXIT
    printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$1" >&2
    exit 1
}

# 4. Privilege & Dependency Checks
# ------------------------------------------------------------------------------
command -v jq &> /dev/null || die "'jq' is missing. Install: sudo pacman -S jq"
command -v hyprctl &> /dev/null || die "'hyprctl' is missing."
[[ $EUID -ne 0 ]] || die "Root detected. Run as standard user for socket access."

# 5. Argument Parsing
# ------------------------------------------------------------------------------
if [[ $# -ne 1 ]]; then
    trap - EXIT
    printf '%s[INFO]%s Usage: %s [+90|-90]\n' "$C_YELLOW" "$C_RESET" "${0##*/}" >&2
    exit 1
fi

DIRECTION=0
case "$1" in
    '+90') DIRECTION=1 ;;
    '-90') DIRECTION=-1 ;;
    *) die "Invalid argument '$1'. Use +90 or -90." ;;
esac

# 6. IPC State Extraction (Source of Truth for Current State)
# ------------------------------------------------------------------------------
MON_STATE=$(hyprctl monitors -j) || die "Failed to query Hyprland IPC."

# Extract focused monitor's Name, Transform, and core fallback geometry.
# Override IFS locally — the global IFS=$'\n\t' excludes spaces from splitting.
IFS=' ' read -r NAME CURRENT_TRANSFORM FALLBACK_WIDTH FALLBACK_HEIGHT FALLBACK_REFRESH FALLBACK_X FALLBACK_Y FALLBACK_SCALE < <(
    jq -r '([.[] | select(.focused)][0] // .[0]) | "\(.name) \(.transform) \(.width) \(.height) \(.refreshRate) \(.x) \(.y) \(.scale)"' <<< "$MON_STATE"
) || die "Failed to parse monitor state."

[[ -n $NAME && $NAME != 'null' ]] || die "No active monitors detected."
[[ $CURRENT_TRANSFORM =~ ^[0-3]$ ]] || die "Unsupported transform: '${CURRENT_TRANSFORM}'. Only standard (0-3) supported."

# Calculate new transform safely
NEW_TRANSFORM=$(( (CURRENT_TRANSFORM + DIRECTION + 4) % 4 ))

# 7. Surgical Config Parsing (Source of Truth for Parameters)
# ------------------------------------------------------------------------------
BASE_RULE=""
RULE_SOURCE="IPC Fallback"

for conf in "${CONFIG_FILES[@]}"; do
    if [[ -f "$conf" ]]; then
        # Use awk to find the last line defining this monitor (last-definition-wins,
        # matching Hyprland's own config semantics)
        matched_line=$(awk -v mon="$NAME" '
            $0 ~ "^[[:space:]]*monitor[[:space:]]*=[[:space:]]*" mon "[[:space:]]*," {
                line = $0
            }
            END { if (line != "") print line }
        ' "$conf")

        if [[ -n "$matched_line" ]]; then
            # Strip inline comments
            matched_line="${matched_line%%#*}"

            # Bash native regex to extract everything after the equals sign
            if [[ "$matched_line" =~ ^[[:space:]]*monitor[[:space:]]*=[[:space:]]*(.*)$ ]]; then
                BASE_RULE="${BASH_REMATCH[1]}"
                # Strip any resulting trailing whitespace efficiently
                BASE_RULE="${BASE_RULE%"${BASE_RULE##*[![:space:]]}"}"
                RULE_SOURCE="$conf"
                break
            fi
        fi
    fi
done

# 8. Payload Assembly
# ------------------------------------------------------------------------------
if [[ -n "$BASE_RULE" ]]; then
    # STRATEGY A: Config Injection
    # Strip any existing 'transform, X' pairs surgically using sed to prevent duplicates
    CLEAN_RULE=$(sed -E 's/,[[:space:]]*transform[[:space:]]*,[[:space:]]*[0-7]//g' <<< "$BASE_RULE")

    # Append the new transform to the pristine config string
    FINAL_PAYLOAD="${CLEAN_RULE}, transform, ${NEW_TRANSFORM}"
else
    # STRATEGY B: IPC Reconstruction (Failsafe for transient/hot-plugged displays)
    # Rebuild the string using exact values, avoiding 'preferred, auto' completely
    FINAL_PAYLOAD="${NAME}, ${FALLBACK_WIDTH}x${FALLBACK_HEIGHT}@${FALLBACK_REFRESH}, ${FALLBACK_X}x${FALLBACK_Y}, ${FALLBACK_SCALE}, transform, ${NEW_TRANSFORM}"
fi

# 9. Execution
# ------------------------------------------------------------------------------
printf '%s[INFO]%s Target: %s%s%s\n' "$C_BLUE" "$C_RESET" "$C_BOLD" "$NAME" "$C_RESET"
printf '%s[INFO]%s Source: %s\n' "$C_BLUE" "$C_RESET" "$RULE_SOURCE"
printf '%s[INFO]%s Payload: %s\n' "$C_YELLOW" "$C_RESET" "$FINAL_PAYLOAD"

if hyprctl keyword monitor "$FINAL_PAYLOAD" > /dev/null; then
    printf '%s[SUCCESS]%s Rotation applied: %d -> %d\n' "$C_GREEN" "$C_RESET" "$CURRENT_TRANSFORM" "$NEW_TRANSFORM"

    if command -v notify-send &> /dev/null; then
        notify-send -a 'System' 'Display Rotated' \
            "$(printf 'Monitor: %s\nTransform: %d\nSource: %s' "$NAME" "$NEW_TRANSFORM" "${RULE_SOURCE##*/}")" \
            -h string:x-canonical-private-synchronous:display-rotate
    fi
else
    die "Hyprland rejected the monitor payload."
fi

trap - EXIT
exit 0
