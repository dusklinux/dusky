#!/usr/bin/env bash
#==============================================================================
# Hyprland Visuals Controller - Ultra-Optimized (Bash 5.3+ / Arch Linux)
# Architecture: Single-Pass IPC, Native Globbing, Zero-Copy Stream Processing,
#               Pure Bash Regex Parsing, Lockless Atomic VFS Swaps.
#==============================================================================

# Strict mode: exit on error, undefined vars, and pipeline failures
set -o errexit
set -o nounset
set -o pipefail

# Enable advanced Bash 5 built-in file globbing
shopt -s globstar nullglob

# --- Configuration ---
readonly CONFIG_FILE="${HOME}/.config/hypr/edit_here/source/appearance.lua"
readonly STATE_FILE="${HOME}/.config/dusky/settings/opacity_blur"

# Mako Targets
readonly MAKO_TEMPLATE="${HOME}/.config/matugen/templates/mako.ini"
readonly MAKO_GENERATED="${HOME}/.config/matugen/generated/mako-colors.ini"

# Rofi Targets
readonly ROFI_TEMPLATE="${HOME}/.config/matugen/templates/rofi-colors.rasi"
readonly ROFI_GENERATED="${HOME}/.config/matugen/generated/rofi-colors.rasi"

# Waybar Targets
readonly WAYBAR_DIR="${HOME}/.config/waybar"

# Visual Constants
readonly OP_ACTIVE_ON="0.85"
readonly OP_INACTIVE_ON="0.85"
readonly OP_SINGLE_ON="1.0"
readonly OP_MAXIMIZED_ON="1.0"

readonly OP_ACTIVE_OFF="1.0"
readonly OP_INACTIVE_OFF="1.0"
readonly OP_SINGLE_OFF="1.0"
readonly OP_MAXIMIZED_OFF="1.0"

# Rofi UI Alpha Values
readonly UI_ALPHA_ON="66"
readonly UI_ALPHA_OFF="ff"

# Mako Global Alpha Values (Section 4 Specific)
readonly MAKO_BG_ALPHA_ON="1a"
readonly MAKO_BORDER_ALPHA_ON="33"
readonly MAKO_PROGRESS_ALPHA_ON="4d"

readonly MAKO_BG_ALPHA_OFF="ff"
readonly MAKO_BORDER_ALPHA_OFF="ff"
readonly MAKO_PROGRESS_ALPHA_OFF="ff"

# --- Global State for Signal Trapping ---
declare -g CURRENT_TEMP_FILE=""

# --- Cascading Signal Interception ---
cleanup_temps() {
    # Check and remove only the currently active temp file if execution aborts
    if [[ -n "${CURRENT_TEMP_FILE}" && -f "${CURRENT_TEMP_FILE}" ]]; then
        rm -f "${CURRENT_TEMP_FILE}"
    fi
}

trap cleanup_temps EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

die() {
    local message="$1"
    printf 'Error: %s\n' "$message" >&2
    command -v notify-send &>/dev/null && notify-send -u critical "Hyprland Error" "$message" 2>/dev/null || true
    exit 1
}

notify() {
    command -v notify-send &>/dev/null && notify-send \
        -h string:x-canonical-private-synchronous:hypr-visuals \
        -t 1500 "Hyprland" "$1" 2>/dev/null || true
}

# --- The Architecture: Stream-Optimized Atomic I/O ---
atomic_sed() {
    local target_file="$1"
    shift 

    local actual_target="${target_file}"
    [[ -L "${target_file}" ]] && actual_target=$(realpath -m "${target_file}")
    
    # Pure Bash dirname emulation to ensure parent directory is writable
    local parent_dir="."
    [[ "${actual_target}" == */* ]] && parent_dir="${actual_target%/*}"
    [[ -w "${actual_target}" && -w "${parent_dir}" ]] || return 0

    CURRENT_TEMP_FILE=$(mktemp "${parent_dir}/.hypr_toggle.XXXXXX") || die "Failed to allocate temp file."

    # Process stream directly to temp file
    if ! sed "$@" "${actual_target}" > "${CURRENT_TEMP_FILE}"; then
        die "Failed to process sed commands on ${actual_target}"
    fi

    # Clone exact permissions instantly
    chmod --reference="${actual_target}" "${CURRENT_TEMP_FILE}" 2>/dev/null || true

    # Atomic swap (instant, uninterruptible rename syscall)
    if ! command mv -f "${CURRENT_TEMP_FILE}" "${actual_target}"; then
        die "Atomic swap failed for ${actual_target}"
    fi
    
    CURRENT_TEMP_FILE=""
}

atomic_awk() {
    local target_file="$1"
    local awk_script="$2"
    local target_state="$3"

    local actual_target="${target_file}"
    [[ -L "${target_file}" ]] && actual_target=$(realpath -m "${target_file}")
    
    local parent_dir="."
    [[ "${actual_target}" == */* ]] && parent_dir="${actual_target%/*}"
    [[ -w "${actual_target}" && -w "${parent_dir}" ]] || return 0

    CURRENT_TEMP_FILE=$(mktemp "${parent_dir}/.hypr_toggle.XXXXXX") || die "Failed to allocate temp file."

    if ! awk -v state="$target_state" "$awk_script" "${actual_target}" > "${CURRENT_TEMP_FILE}"; then
        die "Failed to process awk script on ${actual_target}"
    fi

    chmod --reference="${actual_target}" "${CURRENT_TEMP_FILE}" 2>/dev/null || true

    if ! command mv -f "${CURRENT_TEMP_FILE}" "${actual_target}"; then
        die "Atomic swap failed for ${actual_target}"
    fi
    
    CURRENT_TEMP_FILE=""
}

# Pure Bash Regex Parser (0 external binaries spawned, maximum speed)
get_current_blur_state() {
    local state="off"
    local in_block=0
    local line

    while IFS= read -r line || [[ -n "$line" ]]; do
        # Trim leading whitespace natively
        line="${line#"${line%%[![:space:]]*}"}"
        
        # Fast comment bypass
        [[ "$line" == --* ]] && continue

        if (( in_block == 0 )); then
            # Bash regex match for block entry
            [[ "$line" =~ ^blur[[:space:]]*=[[:space:]]*\{ ]] && in_block=1
        else
            if [[ "$line" =~ ^enabled[[:space:]]*=[[:space:]]*true ]]; then
                state="on"
                break
            elif [[ "$line" =~ ^enabled[[:space:]]*=[[:space:]]*false ]]; then
                state="off"
                break
            elif [[ "$line" == *\}* ]]; then
                break
            fi
        fi
    done < "$CONFIG_FILE" 2>/dev/null || true

    printf '%s' "$state"
}

# --- Pre-flight Checks ---
[[ -e "$CONFIG_FILE" ]] || die "Config file not found: $CONFIG_FILE"

# --- Parse Arguments ---
TARGET_STATE=""
case "${1:-toggle}" in
    on|ON|enable|1|true|yes) TARGET_STATE="on" ;;
    off|OFF|disable|0|false|no) TARGET_STATE="off" ;;
    toggle|"")
        [[ "$(get_current_blur_state)" == "on" ]] && TARGET_STATE="off" || TARGET_STATE="on"
        ;;
    -h|--help|help)
        printf "Usage: %s [on|off|toggle]\n" "${0##*/}"
        exit 0
        ;;
    *)
        printf 'Unknown argument: %s\n' "$1" >&2
        exit 1
        ;;
esac

# --- Define Values ---
if [[ "$TARGET_STATE" == "on" ]]; then
    NEW_ENABLED="true"
    NEW_ACTIVE="$OP_ACTIVE_ON"
    NEW_INACTIVE="$OP_INACTIVE_ON"
    NEW_SINGLE="$OP_SINGLE_ON"
    NEW_MAXIMIZED="$OP_MAXIMIZED_ON"
    NEW_UI_ALPHA="$UI_ALPHA_ON"
    
    NEW_MAKO_BG_ALPHA="$MAKO_BG_ALPHA_ON"
    NEW_MAKO_BORDER_ALPHA="$MAKO_BORDER_ALPHA_ON"
    NEW_MAKO_PROGRESS_ALPHA="$MAKO_PROGRESS_ALPHA_ON"
    
    NOTIFY_MSG="Visuals: Max (Blur/Shadow ON)"
    STATE_STRING="True"
else
    NEW_ENABLED="false"
    NEW_ACTIVE="$OP_ACTIVE_OFF"
    NEW_INACTIVE="$OP_INACTIVE_OFF"
    NEW_SINGLE="$OP_SINGLE_OFF"
    NEW_MAXIMIZED="$OP_MAXIMIZED_OFF"
    NEW_UI_ALPHA="$UI_ALPHA_OFF"
    
    NEW_MAKO_BG_ALPHA="$MAKO_BG_ALPHA_OFF"
    NEW_MAKO_BORDER_ALPHA="$MAKO_BORDER_ALPHA_OFF"
    NEW_MAKO_PROGRESS_ALPHA="$MAKO_PROGRESS_ALPHA_OFF"
    
    NOTIFY_MSG="Visuals: Performance (Blur/Shadow OFF)"
    STATE_STRING="False"
fi

# --- Update State File ---
mkdir -p "${STATE_FILE%/*}"
printf '%s' "$STATE_STRING" > "$STATE_FILE"

# --- Update Config Files ---

# 1. Hyprland Lua Config (Engineered to perfectly span inner tables and target values)
atomic_sed "$CONFIG_FILE" \
    -e "/^[[:space:]]*blur[[:space:]]*=[[:space:]]*{/,/^[[:space:]]*}/ s/^\([[:space:]]*enabled[[:space:]]*=[[:space:]]*\)[a-z][a-z]*/\1${NEW_ENABLED}/" \
    -e "/^[[:space:]]*shadow[[:space:]]*=[[:space:]]*{/,/^[[:space:]]*}/ s/^\([[:space:]]*enabled[[:space:]]*=[[:space:]]*\)[a-z][a-z]*/\1${NEW_ENABLED}/" \
    -e "s/^\([[:space:]]*active_opacity[[:space:]]*=[[:space:]]*\)[0-9][0-9.]*/\1${NEW_ACTIVE}/" \
    -e "s/^\([[:space:]]*inactive_opacity[[:space:]]*=[[:space:]]*\)[0-9][0-9.]*/\1${NEW_INACTIVE}/" \
    -e "/name[[:space:]]*=[[:space:]]*\"single_window_style\"/,/^[[:space:]]*})/ s/^\([[:space:]]*opacity[[:space:]]*=[[:space:]]*\)[0-9.]*/\1${NEW_SINGLE}/" \
    -e "/name[[:space:]]*=[[:space:]]*\"maximized_window_style\"/,/^[[:space:]]*})/ s/^\([[:space:]]*opacity[[:space:]]*=[[:space:]]*\)[0-9.]*/\1${NEW_MAXIMIZED}/"

# 2. Dynamic UI Targets

# Mako (Restricted exclusively to Section 4: GLOBAL MATUGEN COLOR INJECTION)
if [[ -w "$MAKO_TEMPLATE" ]]; then
    atomic_sed "$MAKO_TEMPLATE" \
        -e "/GLOBAL MATUGEN COLOR INJECTION/,/STATE MODES/ s/^\([[:space:]]*background-color={{[^}]*}}\)[0-9a-fA-F]*[[:space:]]*$/\1${NEW_MAKO_BG_ALPHA}/" \
        -e "/GLOBAL MATUGEN COLOR INJECTION/,/STATE MODES/ s/^\([[:space:]]*border-color={{[^}]*}}\)[0-9a-fA-F]*[[:space:]]*$/\1${NEW_MAKO_BORDER_ALPHA}/" \
        -e "/GLOBAL MATUGEN COLOR INJECTION/,/STATE MODES/ s/^\([[:space:]]*progress-color={{[^}]*}}\)[0-9a-fA-F]*[[:space:]]*$/\1${NEW_MAKO_PROGRESS_ALPHA}/"
fi

if [[ -w "$MAKO_GENERATED" ]]; then
    atomic_sed "$MAKO_GENERATED" \
        -e "/GLOBAL MATUGEN COLOR INJECTION/,/STATE MODES/ s/^\([[:space:]]*background-color=#[0-9a-fA-F]\{6\}\)[0-9a-fA-F]*[[:space:]]*$/\1${NEW_MAKO_BG_ALPHA}/" \
        -e "/GLOBAL MATUGEN COLOR INJECTION/,/STATE MODES/ s/^\([[:space:]]*border-color=#[0-9a-fA-F]\{6\}\)[0-9a-fA-F]*[[:space:]]*$/\1${NEW_MAKO_BORDER_ALPHA}/" \
        -e "/GLOBAL MATUGEN COLOR INJECTION/,/STATE MODES/ s/^\([[:space:]]*progress-color=#[0-9a-fA-F]\{6\}\)[0-9a-fA-F]*[[:space:]]*$/\1${NEW_MAKO_PROGRESS_ALPHA}/"
fi

# Rofi (Untouched - Only target surface opacity)
[[ -w "$ROFI_TEMPLATE" ]] && atomic_sed "$ROFI_TEMPLATE" "s/^\([[:space:]]*surface[[:space:]]*:[[:space:]]*{{[^}]*}}\)[0-9a-fA-F]\{2\};/\1${NEW_UI_ALPHA};/"
[[ -w "$ROFI_GENERATED" ]] && atomic_sed "$ROFI_GENERATED" "s/^\([[:space:]]*surface[[:space:]]*:[[:space:]]*#[0-9a-fA-F]\{6\}\)[0-9a-fA-F]\{2\};/\1${NEW_UI_ALPHA};/"

# --- Waybar Recursive Engine ---
if [[ -d "$WAYBAR_DIR" ]]; then
    read -r -d '' AWK_WAYBAR_SCRIPT << 'EOF' || true
        /Remove this line to flip the master switch to OPAQUE/ {
            count++
            if (count % 2 == 1) {
                print "/* WAYBAR_OPAQUE_SWITCH_START" (state == "off" ? " */" : "")
            } else {
                print (state == "off" ? "/* " : "") "WAYBAR_OPAQUE_SWITCH_END */"
            }
            next
        }
        /WAYBAR_OPAQUE_SWITCH_START/ {
            print "/* WAYBAR_OPAQUE_SWITCH_START" (state == "off" ? " */" : "")
            next
        }
        /WAYBAR_OPAQUE_SWITCH_END/ {
            print (state == "off" ? "/* " : "") "WAYBAR_OPAQUE_SWITCH_END */"
            next
        }
        { print }
EOF

    # Leverages Bash 5 '**' globstar, entirely eliminating the external 'find' binary
    for style_file in "$WAYBAR_DIR"/**/style.css; do
        # Evaluates strictly for regular files but correctly resolves symlinks for users using GNU Stow
        [[ -f "$style_file" ]] || continue
        atomic_awk "$style_file" "$AWK_WAYBAR_SCRIPT" "$TARGET_STATE"
    done
fi

# --- Apply Changes at Runtime (Single Batch IPC) ---
if command -v hyprctl &>/dev/null; then
    HYPR_BATCH_CMD="keyword decoration:blur:enabled ${NEW_ENABLED}; keyword decoration:shadow:enabled ${NEW_ENABLED}; keyword decoration:active_opacity ${NEW_ACTIVE}; keyword decoration:inactive_opacity ${NEW_INACTIVE}"
    
    if ! hyprctl --batch "$HYPR_BATCH_CMD" &>/dev/null; then
        printf 'Warning: hyprctl batch command failed. Is Hyprland running?\n' >&2
    fi
    
    # Efficiently reload the config to catch the new Lua window rules without flickering monitors
    hyprctl reload config-only &>/dev/null || true
fi

# Reload dynamic daemons
command -v makoctl &>/dev/null && { makoctl reload &>/dev/null || true; }
command -v pkill &>/dev/null && { pkill -SIGUSR2 waybar || true; }

# --- User Feedback ---
notify "$NOTIFY_MSG"

exit 0
