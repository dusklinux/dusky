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
    "${XDG_CONFIG_HOME:-$HOME/.config}/hypr/edit_here/source/monitors.lua"
    "${XDG_CONFIG_HOME:-$HOME/.config}/hypr/source/monitors.lua"
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
command -v python3 &> /dev/null || die "'python3' is missing."
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

# 7. Locate config file
# ------------------------------------------------------------------------------
CONF_FILE=""
for conf in "${CONFIG_FILES[@]}"; do
    if [[ -f "$conf" ]]; then
        CONF_FILE="$conf"
        break
    fi
done

if [[ -z "$CONF_FILE" ]]; then
    # No config exists yet — create one at the primary edit_here location
    CONF_FILE="${CONFIG_FILES[0]}"
    mkdir -p "$(dirname "$CONF_FILE")"
fi

# 8. Update monitors.lua with new transform and reload
# ------------------------------------------------------------------------------
printf '%s[INFO]%s Target: %s%s%s | Transform: %d -> %d\n' \
    "$C_BLUE" "$C_RESET" "$C_BOLD" "$NAME" "$C_RESET" "$CURRENT_TRANSFORM" "$NEW_TRANSFORM"
printf '%s[INFO]%s Config: %s\n' "$C_BLUE" "$C_RESET" "$CONF_FILE"

python3 - "$CONF_FILE" "$NAME" "$NEW_TRANSFORM" <<'PYEOF' \
    || die "Failed to update monitors.lua"
import sys, re, os, tempfile

conf_path, mon_name, new_transform = sys.argv[1], sys.argv[2], sys.argv[3]

try:
    with open(conf_path, 'r') as f:
        text = f.read()
except FileNotFoundError:
    text = ''

found = False

def replacer(m):
    global found
    line = m.group(0)
    if f'output = "{mon_name}"' not in line:
        return line
    found = True
    # Remove first, then insert — reversing order would cause the removal regex
    # to eat the value just inserted, leaving no transform field at all.
    line = re.sub(r',\s*transform\s*=\s*\d+', '', line)
    line = re.sub(r'(\s*\}\s*\)\s*)$', f', transform = {new_transform}\\1', line)
    return line

text = re.sub(r'^.*hl\.monitor\s*\(.*$', replacer, text, flags=re.MULTILINE)

if not found:
    text += f'\nhl.monitor({{ output = "{mon_name}", mode = "preferred", position = "auto", scale = 1, transform = {new_transform} }})\n'

try:
    orig_mode = os.stat(conf_path).st_mode
except FileNotFoundError:
    orig_mode = 0o644

fd, tmp = tempfile.mkstemp(dir=os.path.dirname(conf_path), prefix='.monitors.lua.tmp.')
try:
    with os.fdopen(fd, 'w') as f:
        f.write(text)
    os.chmod(tmp, orig_mode)
    os.replace(tmp, conf_path)
except Exception as e:
    os.remove(tmp)
    sys.stderr.write(f'Atomic write failed: {e}\n')
    sys.exit(1)
PYEOF

# 9. Apply via reload
# ------------------------------------------------------------------------------
if hyprctl reload > /dev/null; then
    printf '%s[SUCCESS]%s Rotation applied: %d -> %d\n' "$C_GREEN" "$C_RESET" "$CURRENT_TRANSFORM" "$NEW_TRANSFORM"

    if command -v notify-send &> /dev/null; then
        notify-send -a 'System' 'Display Rotated' \
            "$(printf 'Monitor: %s\nTransform: %d' "$NAME" "$NEW_TRANSFORM")" \
            -h string:x-canonical-private-synchronous:display-rotate
    fi
else
    die "hyprctl reload failed."
fi

trap - EXIT
exit 0
