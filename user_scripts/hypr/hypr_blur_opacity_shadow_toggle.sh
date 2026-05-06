#!/usr/bin/env bash
#==============================================================================
# Hyprland Visuals Controller (Blur, Shadow, Opacity)
# Architecture: Zero-Corruption Atomic Writes, Symlink Safe, Regex Parsing,
#               Modular UI Integration (Mako, Rofi, Waybar-ready)
#==============================================================================

# Strict mode: exit on error, undefined vars, and pipeline failures
set -o errexit
set -o nounset
set -o pipefail

# --- Configuration ---
readonly CONFIG_FILE="${HOME}/.config/hypr/edit_here/source/appearance.conf"
readonly STATE_FILE="${HOME}/.config/dusky/settings/opacity_blur"

# Mako Targets
readonly MAKO_TEMPLATE="${HOME}/.config/matugen/templates/mako"
readonly MAKO_GENERATED="${HOME}/.config/matugen/generated/mako-colors"

# Rofi Targets
readonly ROFI_TEMPLATE="${HOME}/.config/matugen/templates/rofi-colors.rasi"
readonly ROFI_GENERATED="${HOME}/.config/matugen/generated/rofi-colors.rasi"

# Waybar Targets
readonly WAYBAR_DIR="${HOME}/.config/waybar"

# Visual Constants
readonly OP_ACTIVE_ON="0.8"
readonly OP_INACTIVE_ON="0.6"
readonly OP_ACTIVE_OFF="1.0"
readonly OP_INACTIVE_OFF="1.0"

# UI Component Alpha Constants (Hex)
# When Blur is ON, UI components drop to 66 (40% transparent, for maximum glass effect).
# When Blur is OFF, UI components are ff (100% opaque).
readonly UI_ALPHA_ON="66"
readonly UI_ALPHA_OFF="ff"

# --- Global State for Signal Trapping ---
declare -a TEMP_FILES_TO_CLEAN=()

# --- Helper Functions ---

cleanup_temps() {
    for tmp in "${TEMP_FILES_TO_CLEAN[@]}"; do
        [[ -f "$tmp" ]] && rm -f "$tmp"
    done
}

# Cascading Signal Interception
trap cleanup_temps EXIT
trap 'cleanup_temps; exit 129' HUP
trap 'cleanup_temps; exit 130' INT
trap 'cleanup_temps; exit 143' TERM

die() {
    local message="$1"
    printf 'Error: %s\n' "$message" >&2
    if command -v notify-send &>/dev/null; then
        notify-send "Hyprland Error" "$message" 2>/dev/null || true
    fi
    exit 1
}

notify() {
    local message="$1"
    if command -v notify-send &>/dev/null; then
        notify-send \
            -h string:x-canonical-private-synchronous:hypr-visuals \
            -t 1500 \
            "Hyprland" "$message" 2>/dev/null || true
    fi
}

# --- The Architecture: Atomic, Symlink-Safe Text Processing ---
atomic_sed() {
    local target_file="$1"
    shift # Remaining arguments are sed parameters

    local actual_target="${target_file}"
    if [[ -L "${target_file}" ]]; then
        actual_target=$(realpath -m "${target_file}")
    fi

    [[ -w "${actual_target}" ]] || return 0

    local target_dir="${actual_target%/*}"
    local temp_file
    temp_file=$(mktemp "${target_dir}/.hypr_toggle.XXXXXX") || die "Failed to allocate temp file."
    
    TEMP_FILES_TO_CLEAN+=("${temp_file}")

    command cp -pf "${actual_target}" "${temp_file}"

    if ! sed -i "$@" "${temp_file}" 2>&1; then
        die "Failed to process sed commands on ${actual_target}"
    fi

    sync "${temp_file}" || true

    if ! command mv -f "${temp_file}" "${actual_target}"; then
        die "Atomic swap failed for ${actual_target}"
    fi
}

atomic_awk() {
    local target_file="$1"
    local awk_script="$2"
    local target_state="$3"

    local actual_target="${target_file}"
    if [[ -L "${target_file}" ]]; then
        actual_target=$(realpath -m "${target_file}")
    fi

    [[ -w "${actual_target}" ]] || return 0

    local target_dir="${actual_target%/*}"
    local temp_file
    temp_file=$(mktemp "${target_dir}/.hypr_toggle.XXXXXX") || die "Failed to allocate temp file."
    
    TEMP_FILES_TO_CLEAN+=("${temp_file}")

    command cp -pf "${actual_target}" "${temp_file}"

    if ! awk -v state="$target_state" "$awk_script" "${actual_target}" > "${temp_file}"; then
        die "Failed to process awk script on ${actual_target}"
    fi

    sync "${temp_file}" || true

    if ! command mv -f "${temp_file}" "${actual_target}"; then
        die "Atomic swap failed for ${actual_target}"
    fi
}

# Robustly detect current blur state from config file using awk
get_current_blur_state() {
    local state
    local actual_config="${CONFIG_FILE}"
    [[ -L "${CONFIG_FILE}" ]] && actual_config=$(realpath -m "${CONFIG_FILE}")

    state=$(awk '
        /^[[:space:]]*blur[[:space:]]*\{/ { in_block = 1; next }
        in_block && /^[[:space:]]*enabled[[:space:]]*=[[:space:]]*true/  { found = "on" }
        in_block && /^[[:space:]]*enabled[[:space:]]*=[[:space:]]*false/ { found = "off" }
        in_block && /\}/  { in_block = 0 }
        END { print (found ? found : "off") }
    ' "$actual_config" 2>/dev/null) || state="off"
    printf '%s' "$state"
}

show_help() {
    cat <<EOF
Usage: ${0##*/} [OPTION]

Control Hyprland visual effects (blur, shadow, opacity).
Includes atomic, symlink-safe configuration writes for UI targets.

Options:
  on, enable, 1, true     Enable blur, shadow, and transparency
  off, disable, 0, false  Disable blur/shadow, set opacity to 1.0
  toggle                  Toggle based on current state (default)
  -h, --help              Show help
EOF
}

# --- Pre-flight Checks ---

[[ -e "$CONFIG_FILE" ]] || die "Config file not found: $CONFIG_FILE"
command -v hyprctl &>/dev/null || die "hyprctl not found in PATH."

# --- Parse Arguments ---

TARGET_STATE=""
case "${1:-toggle}" in
    on|ON|enable|1|true|yes) TARGET_STATE="on" ;;
    off|OFF|disable|0|false|no) TARGET_STATE="off" ;;
    toggle|"")
        if [[ "$(get_current_blur_state)" == "on" ]]; then
            TARGET_STATE="off"
        else
            TARGET_STATE="on"
        fi
        ;;
    -h|--help|help)
        show_help
        exit 0
        ;;
    *)
        printf 'Unknown argument: %s\n\n' "$1" >&2
        show_help >&2
        exit 1
        ;;
esac

# --- Define Values Based on Target State ---

declare NEW_ENABLED NEW_ACTIVE NEW_INACTIVE NEW_UI_ALPHA NOTIFY_MSG STATE_STRING

if [[ "$TARGET_STATE" == "on" ]]; then
    NEW_ENABLED="true"
    NEW_ACTIVE="$OP_ACTIVE_ON"
    NEW_INACTIVE="$OP_INACTIVE_ON"
    NEW_UI_ALPHA="$UI_ALPHA_ON"
    NOTIFY_MSG="Visuals: Max (Blur/Shadow ON)"
    STATE_STRING="True"
else
    NEW_ENABLED="false"
    NEW_ACTIVE="$OP_ACTIVE_OFF"
    NEW_INACTIVE="$OP_INACTIVE_OFF"
    NEW_UI_ALPHA="$UI_ALPHA_OFF"
    NOTIFY_MSG="Visuals: Performance (Blur/Shadow OFF)"
    STATE_STRING="False"
fi

# --- Update State File ---

mkdir -p "$(dirname "$STATE_FILE")"
printf '%s' "$STATE_STRING" > "$STATE_FILE"

# --- Update Config Files (Using Atomic Pipeline) ---

# 1. Update Hyprland Config
atomic_sed "$CONFIG_FILE" \
    -e "/^[[:space:]]*blur[[:space:]]*{/,/}/ s/\(enabled[[:space:]]*=[[:space:]]*\)[a-z][a-z]*/\1${NEW_ENABLED}/" \
    -e "/^[[:space:]]*shadow[[:space:]]*{/,/}/ s/\(enabled[[:space:]]*=[[:space:]]*\)[a-z][a-z]*/\1${NEW_ENABLED}/" \
    -e "s/^\([[:space:]]*active_opacity[[:space:]]*=[[:space:]]*\)[0-9][0-9.]*/\1${NEW_ACTIVE}/" \
    -e "s/^\([[:space:]]*inactive_opacity[[:space:]]*=[[:space:]]*\)[0-9][0-9.]*/\1${NEW_INACTIVE}/"


# 2. Update Dynamic UI Targets

# --- Mako ---
if [[ -w "$MAKO_TEMPLATE" ]]; then
    atomic_sed "$MAKO_TEMPLATE" "s/^\([[:space:]]*background-color={{[^}]*}}\)[0-9a-fA-F]\{2\}/\1${NEW_UI_ALPHA}/"
fi
if [[ -w "$MAKO_GENERATED" ]]; then
    atomic_sed "$MAKO_GENERATED" "s/^\([[:space:]]*background-color=#[0-9a-fA-F]\{6\}\)[0-9a-fA-F]\{2\}/\1${NEW_UI_ALPHA}/"
fi

# --- Rofi ---
if [[ -w "$ROFI_TEMPLATE" ]]; then
    atomic_sed "$ROFI_TEMPLATE" "s/^\([[:space:]]*surface[[:space:]]*:[[:space:]]*{{[^}]*}}\)[0-9a-fA-F]\{2\};/\1${NEW_UI_ALPHA};/"
fi
if [[ -w "$ROFI_GENERATED" ]]; then
    atomic_sed "$ROFI_GENERATED" "s/^\([[:space:]]*surface[[:space:]]*:[[:space:]]*#[0-9a-fA-F]\{6\}\)[0-9a-fA-F]\{2\};/\1${NEW_UI_ALPHA};/"
fi

# --- Waybar Recursive Engine ---
if [[ -d "$WAYBAR_DIR" ]]; then
    # Awk state-machine to auto-migrate legacy strings to structural markers and toggle cleanly.
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

    # find -type f avoids processing root symlinks twice by isolating the actual structural files
    while IFS= read -r -d '' style_file; do
        atomic_awk "$style_file" "$AWK_WAYBAR_SCRIPT" "$TARGET_STATE"
    done < <(find "$WAYBAR_DIR" -type f -name "style.css" -print0 2>/dev/null)
fi


# --- Apply Changes at Runtime ---

declare -a HYPR_CMDS=(
    "decoration:blur:enabled ${NEW_ENABLED}"
    "decoration:shadow:enabled ${NEW_ENABLED}"
    "decoration:active_opacity ${NEW_ACTIVE}"
    "decoration:inactive_opacity ${NEW_INACTIVE}"
)

hypr_errors=0
for cmd in "${HYPR_CMDS[@]}"; do
    # shellcheck disable=SC2086
    if ! hyprctl keyword $cmd &>/dev/null; then
        ((hypr_errors++)) || true
    fi
done

if ((hypr_errors > 0)); then
    printf 'Warning: %d hyprctl command(s) failed. Is Hyprland running?\n' "$hypr_errors" >&2
fi

# Reload dynamic daemons
if command -v makoctl &>/dev/null; then
    makoctl reload &>/dev/null || printf 'Warning: makoctl reload failed.\n' >&2
fi

# Trigger Waybar hot-reload to apply CSS changes instantaneously
if command -v pkill &>/dev/null; then
    pkill -SIGUSR2 waybar || true
fi

# --- User Feedback ---

notify "$NOTIFY_MSG"

exit 0
