#!/bin/bash
# Toggle wlogout with monitor-scaled GTK styling in a Hyprland session.

set -euo pipefail

# ──────────────────────────────────────────────────────────────
# 1. Configuration & Constants
# ──────────────────────────────────────────────────────────────
readonly CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/wlogout"
readonly LAYOUT_FILE="${CONFIG_DIR}/layout"
readonly ICON_DIR="${CONFIG_DIR}/icons"
readonly MATUGEN_COLORS="${XDG_CONFIG_HOME:-$HOME/.config}/matugen/generated/wlogout-colors.css"
TMP_CSS=''
WLOGOUT_PID=''
LAUNCHING=0
STATE=''

# Reference: 1080p @ 1.0 scale settings
readonly REF_HEIGHT=1080
readonly BASE_FONT_SIZE=20
readonly BASE_BUTTON_RAD=20
readonly BASE_ACTIVE_RAD=25
readonly BASE_MARGIN=50
readonly BASE_HOVER_OFFSET=15
readonly BASE_COL_SPACING=2

# ──────────────────────────────────────────────────────────────
# 2. Strict Environment & Dependency Validation
# ──────────────────────────────────────────────────────────────
if [[ -z "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]]; then
    echo "ERROR: Not running inside a Hyprland session." >&2
    exit 1
fi

if [[ -z "${XDG_RUNTIME_DIR:-}" || ! -d "$XDG_RUNTIME_DIR" || ! -w "$XDG_RUNTIME_DIR" ]]; then
    echo "ERROR: XDG_RUNTIME_DIR is not available or writable." >&2
    exit 1
fi

readonly RUNTIME_DIR="$XDG_RUNTIME_DIR"
readonly LOCK_FILE="${RUNTIME_DIR}/wlogout-launch-${HYPRLAND_INSTANCE_SIGNATURE}.lock"
readonly PID_FILE="${RUNTIME_DIR}/wlogout-launch-${HYPRLAND_INSTANCE_SIGNATURE}.pid"

# Enforce dependency availability
for cmd in hyprctl jq wlogout flock mktemp rm cat; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: Required command '$cmd' not found in PATH." >&2
        exit 1
    fi
done

# ──────────────────────────────────────────────────────────────
# 3. Lifecycle Management (Traps & Cleanup)
# ──────────────────────────────────────────────────────────────
# /proc/PID/stat field 22 distinguishes a live owner from a reused PID.
process_start_time() {
    local stat
    local -a fields
    IFS= read -r stat < "/proc/$1/stat" || return
    read -r -a fields <<< "${stat##*) }"
    REPLY=${fields[19]:-}
    [[ "$REPLY" =~ ^[0-9]+$ ]]
}
# shellcheck disable=SC2329 # Invoked by the EXIT trap.
cleanup() {
    trap '' HUP INT TERM
    # A signal can arrive after spawning the child but before assigning $!.
    if (( LAUNCHING )); then
        WLOGOUT_PID=${!:-}
    fi
    # Reap the child before removing the stylesheet it may still be using.
    if [[ -n "$WLOGOUT_PID" ]]; then
        kill "$WLOGOUT_PID" 2>/dev/null || true
        wait "$WLOGOUT_PID" 2>/dev/null || true
    fi
    [[ -n "$TMP_CSS" ]] || return 0

    # Serialize ownership checks AND removal against a replacement launch.
    flock --exclusive "$LOCK_FD"
    if [[ -f "$PID_FILE" ]]; then
        local recorded_pid=''
        IFS= read -r recorded_pid < "$PID_FILE" || true
        if [[ -n "$STATE" && "$recorded_pid" == "$STATE" ]]; then
            rm -f -- "$PID_FILE"
        fi
    fi
    rm -f -- "$TMP_CSS"
}
trap 'cleanup' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# ──────────────────────────────────────────────────────────────
# 4. Concurrency & Toggle Logic
# ──────────────────────────────────────────────────────────────
exec {LOCK_FD}> "$LOCK_FILE"
flock --exclusive "$LOCK_FD"

if [[ -f "$PID_FILE" ]]; then
    existing_pid=''
    existing_start=''
    existing_css=''
    read -r existing_pid existing_start existing_css < "$PID_FILE" || true

    # The child's start time is stable across exec and prevents PID reuse.
    if [[ "$existing_pid" =~ ^[1-9][0-9]*$ && "$existing_pid" != "$$" \
        && "$existing_start" =~ ^[0-9]+$ && -n "$existing_css" ]] \
        && { process_start_time "$existing_pid"; } 2>/dev/null \
        && [[ "$REPLY" == "$existing_start" ]]; then
        kill "$existing_pid" 2>/dev/null || true
        rm -f -- "$PID_FILE"
        exit 0
    fi
    # Purge stale state
    rm -f -- "$PID_FILE"
fi

# Closing an existing menu must still work if its assets have disappeared.
for file in "$LAYOUT_FILE" "$MATUGEN_COLORS"; do
    if [[ ! -f "$file" || ! -r "$file" ]]; then
        echo "ERROR: Required file is not readable: $file" >&2
        exit 1
    fi
done

# ──────────────────────────────────────────────────────────────
# 5. Asset Generation
# ──────────────────────────────────────────────────────────────
TMP_CSS=$(mktemp --tmpdir="$RUNTIME_DIR" --suffix=.css "wlogout-${HYPRLAND_INSTANCE_SIGNATURE}.XXXXXX")

if ! CALC_VARS=$(hyprctl -j monitors 2>/dev/null | jq -er \
    --argjson reference "$REF_HEIGHT" \
    --argjson sizes "[$BASE_FONT_SIZE,$BASE_BUTTON_RAD,$BASE_ACTIVE_RAD,$BASE_MARGIN,$BASE_HOVER_OFFSET,$BASE_COL_SPACING]" '
    def positive: type == "number" and isfinite and . > 0;
    (first(.[] | select(.focused == true)) // .[0] // {})
    | .transform as $transform
    | (if ([1, 3, 5, 7] | index($transform)) then .width else .height end) as $height
    | (if ($height | positive) then $height else $reference end) as $height
    | (if (.scale | positive) then .scale else 1 end) as $scale
    | ([0.5, ($height / $scale / $reference), 2] | sort | .[1]) as $ratio
    | $sizes | map(. * $ratio | floor) | @tsv
' 2>/dev/null); then
    CALC_VARS="$BASE_FONT_SIZE $BASE_BUTTON_RAD $BASE_ACTIVE_RAD $BASE_MARGIN $BASE_HOVER_OFFSET $BASE_COL_SPACING"
fi

read -r FONT_SIZE BTN_RAD ACT_RAD MARGIN HOVER_OFFSET COL_SPACING <<< "$CALC_VARS"
HOVER_MARGIN=$(( MARGIN - HOVER_OFFSET ))

# Escape CSS strings, including paths with quotes, backslashes or newlines.
css_escape() {
    local value=$1
    value=${value//\\/\\\\}
    value=${value//\"/\\\"}
    value=${value//$'\n'/\\a }
    value=${value//$'\r'/\\d }
    value=${value//$'\f'/\\c }
    printf -v "$2" '%s' "$value"
}
css_escape "$MATUGEN_COLORS" COLORS_CSS
css_escape "$ICON_DIR" ICON_CSS

cat > "$TMP_CSS" <<EOF
/* Import Matugen Colors */
@import url("${COLORS_CSS}");

window {
    background-color: rgba(0, 0, 0, 0.5);
    font-family: "JetBrainsMono Nerd Font", "Roboto", sans-serif;
    font-size: ${FONT_SIZE}px;
}

button {
    color: @on_secondary_container;
    background-color: @secondary_container;
    outline-style: none;
    border: none;
    border-radius: ${BTN_RAD}px;
    margin: ${MARGIN}px 0;
    box-shadow: none;
    text-shadow: none;
    background-repeat: no-repeat;
    background-position: center;
    background-size: 25%;
    transition:
        background-size 0.3s cubic-bezier(.55, 0.0, .28, 1.682),
        margin 0.3s cubic-bezier(.55, 0.0, .28, 1.682),
        border-radius 0.3s cubic-bezier(.55, 0.0, .28, 1.682),
        background-color 0.3s ease;
}

button:focus {
    background-color: @tertiary_container;
    color: @on_tertiary_container;
    background-size: 30%;
}

button:hover {
    background-color: @primary;
    color: @on_primary;
    background-size: 40%;
    border-radius: ${ACT_RAD}px;
    margin: ${HOVER_MARGIN}px 0;
}

#lock { background-image: image(url("${ICON_CSS}/lock_white.png"), url("/usr/share/wlogout/icons/lock.png")); }

#logout { background-image: image(url("${ICON_CSS}/logout_white.png"), url("/usr/share/wlogout/icons/logout.png")); }

#suspend { background-image: image(url("${ICON_CSS}/suspend_white.png"), url("/usr/share/wlogout/icons/suspend.png")); }

#shutdown { background-image: image(url("${ICON_CSS}/shutdown_white.png"), url("/usr/share/wlogout/icons/shutdown.png")); }

#soft-reboot { background-image: image(url("${ICON_CSS}/soft-reboot_white.png"), url("/usr/share/wlogout/icons/reboot.png")); }

#reboot { background-image: image(url("${ICON_CSS}/reboot_white.png"), url("/usr/share/wlogout/icons/reboot.png")); }
EOF

# ──────────────────────────────────────────────────────────────
# 6. Launch & Wait
# ──────────────────────────────────────────────────────────────
LAUNCHING=1
(
    # The child must not keep the wrapper's lock descriptor open.
    exec {LOCK_FD}>&-
    exec wlogout \
        --layout "$LAYOUT_FILE" \
        --css "$TMP_CSS" \
        --protocol layer-shell \
        --buttons-per-row 6 \
        --column-spacing "$COL_SPACING" \
        --row-spacing 0 \
        "$@"
) &
WLOGOUT_PID=$!
LAUNCHING=0

# Publish the child identity while holding the lock. A child that has already
# exited needs no state; wait below still preserves its exit status.
if process_start_time "$WLOGOUT_PID" 2>/dev/null; then
    STATE="$WLOGOUT_PID $REPLY $TMP_CSS"
    printf '%s\n' "$STATE" > "$PID_FILE"
fi

# Release the launch lock; cleanup reacquires it when the menu closes.
flock --unlock "$LOCK_FD"

# Preserve wlogout's exit status, including launch failures.
status=0
wait "$WLOGOUT_PID" || status=$?
WLOGOUT_PID=''
exit "$status"
