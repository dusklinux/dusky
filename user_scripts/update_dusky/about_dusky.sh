#!/usr/bin/env bash
# ==============================================================================
#  DUSKY SETUP BANNER (ANIMATED)
#  Optimized for Kitty Terminal & Arch/Hyprland Ecosystems
#  Author: Dusk
# ==============================================================================

set -o errexit
set -o nounset
set -o pipefail

# --- CONFIGURATION ---
# Adhere to XDG Base Directory specification for config lookup
readonly CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/dusky"
readonly VERSION_FILE="${CONFIG_DIR}/version"

# Default fallback if file is missing/empty
DUSKY_VERSION="v2.4.0"

# Dynamically read version if file exists and is readable
if [[ -r "$VERSION_FILE" ]]; then
    # Read first line, trim leading/trailing whitespace via read behavior
    # '|| true' ensures script doesn't exit if file is empty (read returns non-zero on empty/no-newline)
    read -r file_ver < "$VERSION_FILE" || true
    if [[ -n "${file_ver:-}" ]]; then
        DUSKY_VERSION="$file_ver"
    fi
fi
readonly DUSKY_VERSION

readonly COLOR_START="#7F00FF"  # Deep Twilight Purple
readonly COLOR_END="#E100FF"    # Sunset Pink/Orange
readonly ANIMATION_DURATION_SEC="1.5"
readonly FRAME_DELAY="0.03"

# --- TERMINAL SETUP ---
# Prefer shell's COLUMNS variable, fallback to tput, then 80
TERM_COLS="${COLUMNS:-}"
if [[ -z "$TERM_COLS" ]]; then
    TERM_COLS=$(tput cols 2>/dev/null) || TERM_COLS=80
fi
readonly TERM_COLS

# --- CURSOR STATE & CLEANUP ---
declare -i CURSOR_HIDDEN=0

cleanup() {
    # Always attempt to restore cursor; tput cnorm is idempotent
    tput cnorm 2>/dev/null || true
}
trap cleanup EXIT

# --- ASCII ART DEFINITION ---
# Using mapfile avoids the non-zero exit status of `read -d ''`
declare -a ASCII_LINES
mapfile -t ASCII_LINES <<'EOF'
██████╗ ██╗   ██╗███████╗██╗  ██╗██╗   ██╗
██╔══██╗██║   ██║██╔════╝██║ ██╔╝╚██╗ ██╔╝
██║  ██║██║   ██║███████╗█████╔╝  ╚████╔╝
██║  ██║██║   ██║╚════██║██╔═██╗   ╚██╔╝
██████╔╝╚██████╔╝███████║██║  ██╗   ██║
╚═════╝  ╚═════╝ ╚══════╝╚═╝  ╚═╝   ╚═╝
EOF
readonly -a ASCII_LINES
readonly LINE_COUNT="${#ASCII_LINES[@]}"

# --- PRE-COMPUTE RGB VALUES (once, not per-frame) ---
hex_to_rgb() {
    local hex="${1#\#}"
    # Bash arithmetic handles 0x prefix for hex conversion
    printf '%d %d %d' "0x${hex:0:2}" "0x${hex:2:2}" "0x${hex:4:2}"
}

read -r SR SG SB <<< "$(hex_to_rgb "$COLOR_START")"
read -r ER EG EB <<< "$(hex_to_rgb "$COLOR_END")"
readonly SR SG SB ER EG EB

# --- RENDERING FUNCTIONS ---

# Renders the ASCII art block with animated gradient coloring.
# Arguments: $1 = phase (float 0.0-1.0+ for gradient offset)
render_art() {
    local phase="$1"

    # Join array into newline-separated string without trailing newline
    local IFS=$'\n'
    local art="${ASCII_LINES[*]}"

    awk -v term_cols="$TERM_COLS" \
        -v art="$art" \
        -v sr="$SR" -v sg="$SG" -v sb="$SB" \
        -v er="$ER" -v eg="$EG" -v eb="$EB" \
        -v phase="$phase" \
        '
        function lerp(v1, v2, p) { return int(v1 + (v2 - v1) * p) }

        BEGIN {
            n = split(art, lines, "\n")
            divisor = (n > 1) ? (n - 1) : 1

            for (i = 1; i <= n; i++) {
                if (lines[i] == "") continue

                # Gradient position with phase offset for animation
                progress = ((i - 1) / divisor) + phase

                # Sine wave for smooth color oscillation (absolute value for ping-pong)
                val = sin(progress * 3.14159265)
                if (val < 0) val = -val

                r = lerp(sr, er, val)
                g = lerp(sg, eg, val)
                b = lerp(sb, eb, val)

                # Center the line
                len = length(lines[i])
                pad = int((term_cols - len) / 2)
                if (pad < 0) pad = 0

                # POSIX-compliant: %*s uses next arg as width
                printf "%*s\033[38;2;%d;%d;%dm%s\033[0m\n", pad, "", r, g, b, lines[i]
            }
        }'
}

# Draws the version footer centered below the banner.
draw_footer() {
    local -r footer_color='\033[38;2;90;90;90m'
    local -r highlight_color='\033[38;2;200;200;200m'
    local -r reset='\033[0m'

    local -r ver_text=":: ${DUSKY_VERSION} ::"
    local -i pad=$(( (TERM_COLS - ${#ver_text}) / 2 ))
    (( pad < 0 )) && pad=0

    printf '\n%*s%b:: %b%s%b ::%b\n\n' \
        "$pad" "" \
        "$footer_color" \
        "$highlight_color" \
        "$DUSKY_VERSION" \
        "$footer_color" \
        "$reset"
}

# --- MAIN EXECUTION ---

main() {
    # Hide cursor for clean animation
    tput civis
    CURSOR_HIDDEN=1

    printf '\n'  # Top spacer

    # Pre-calculate ALL animation phases with a SINGLE awk call
    # This avoids spawning awk inside the animation loop
    local -a phases
    mapfile -t phases < <(
        awk -v dur="$ANIMATION_DURATION_SEC" -v delay="$FRAME_DELAY" '
            BEGIN {
                total = int(dur / delay + 0.5)
                for (i = 0; i < total; i++) printf "%.4f\n", i / total
            }'
    )
    local -ri total_frames="${#phases[@]}"

    # Animation loop
    local -i frame
    for (( frame = 0; frame < total_frames; frame++ )); do
        render_art "${phases[frame]}"

        # Sleep and reposition cursor (skip on final frame)
        if (( frame < total_frames - 1 )); then
            sleep "$FRAME_DELAY"
            tput cuu "$LINE_COUNT"
        fi
    done

    # Final static render at balanced gradient position (phase 0.5)
    tput cuu "$LINE_COUNT"
    render_art "0.5"

    draw_footer

    # Restore cursor visibility
    tput cnorm
    CURSOR_HIDDEN=0
}

main "$@"
