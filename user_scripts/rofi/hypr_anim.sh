#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Hyprland Animation Switcher for Rofi
# -----------------------------------------------------------------------------
# Strict Mode:
# -u: Error on unset variables (catches typos)
# -o pipefail: Pipeline fails if any command fails
set -u
set -o pipefail

# -----------------------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------------------
# Use readonly for constants to prevent accidental overwrites
readonly CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}"
readonly ANIM_DIR="$CONFIG_DIR/hypr/source/animations"
readonly LINK_DIR="$ANIM_DIR/active"
readonly DEST_FILE="$LINK_DIR/active.lua"
readonly STATE_FILE="$CONFIG_DIR/dusky/settings/dusky_animiation"
readonly FALLBACK_ANIM="horizontal_dusky.lua"

# Visual Assets (Nerd Fonts)
readonly ICON_ACTIVE=""   # Checkmark
readonly ICON_FILE=""     # File
readonly ICON_ERROR=""    # Warning

# -----------------------------------------------------------------------------
# HELPER FUNCTIONS
# -----------------------------------------------------------------------------

notify_user() {
    local title="$1"
    local message="$2"
    local urgency="${3:-low}"
    if command -v notify-send &>/dev/null; then
        notify-send -u "$urgency" -a "Hyprland Animations" "$title" "$message"
    fi
}

reload_hyprland() {
    # Silence output to prevent polluting Rofi's stream
    if command -v hyprctl &>/dev/null; then
        hyprctl reload &>/dev/null
    fi
}

# Sanitize filenames for Rofi's Pango markup
escape_markup() {
    local s="$1"
    s="${s//&/&amp;}"
    s="${s//</&lt;}"
    s="${s//>/&gt;}"
    s="${s//\"/&quot;}"
    s="${s//\'/&apos;}"
    printf '%s' "$s"
}

# -----------------------------------------------------------------------------
# EXECUTION LOGIC (Selection Made or Flags)
# -----------------------------------------------------------------------------

# Handle the --current restoration flag (with fallback logic)
if [[ "${1:-}" == "--current" ]]; then
    target_anim=""

    # 1. Attempt to read existing valid state
    if [[ -f "$STATE_FILE" ]]; then
        saved_anim=$(<"$STATE_FILE")
        if [[ -n "$saved_anim" && -f "$saved_anim" ]]; then
            target_anim="$saved_anim"
        fi
    fi

    # 2. Fallback if no valid state was found
    if [[ -z "$target_anim" ]]; then
        target_anim="$ANIM_DIR/$FALLBACK_ANIM"
        if [[ ! -f "$target_anim" ]]; then
            notify_user "Error" "Fallback animation missing: $target_anim" "critical"
            exit 1
        fi
    fi

    # 3. Apply target_anim and save state
    mkdir -p -- "$LINK_DIR" 2>/dev/null
    rm -f -- "$DEST_FILE"
    
    if cp -- "$target_anim" "$DEST_FILE"; then
        # Ensure state directory exists and write current state
        mkdir -p -- "${STATE_FILE%/*}" 2>/dev/null
        printf '%s\n' "$target_anim" > "$STATE_FILE"

        reload_hyprland
        exit 0
    else
        notify_user "Failure" "Could not re-apply configuration." "critical"
        exit 1
    fi
fi

selection="${ROFI_INFO:-}"

# Fallback: Handle manual CLI usage or older Rofi versions
if [[ -z "$selection" && -n "${1:-}" ]]; then
    # Use printf to safely handle inputs starting with dashes
    clean_name=$(printf '%s' "$1" | sed 's/<[^>]*>//g' | xargs -r)
    selection="$ANIM_DIR/$clean_name"
fi

if [[ -n "$selection" ]]; then
    if [[ ! -f "$selection" ]]; then
        notify_user "Error" "File not found: $selection" "critical"
        exit 1
    fi

    # Ensure target directory exists
    if ! mkdir -p -- "$LINK_DIR" 2>/dev/null; then
        notify_user "Error" "Cannot create directory: $LINK_DIR" "critical"
        exit 1
    fi

    # ATOMIC-ISH UPDATE
    rm -f -- "$DEST_FILE"

    if cp -- "$selection" "$DEST_FILE"; then
        # Save state for the --current flag
        mkdir -p -- "${STATE_FILE%/*}" 2>/dev/null
        printf '%s\n' "$selection" > "$STATE_FILE"

        # Use parameter expansion for basename (faster than subshell)
        filename="${selection##*/}"
        reload_hyprland
        notify_user "Success" "Switched to: $filename"
        exit 0
    else
        notify_user "Failure" "Could not copy configuration." "critical"
        exit 1
    fi
fi

# -----------------------------------------------------------------------------
# MENU GENERATION (No Selection)
# -----------------------------------------------------------------------------

# Rofi Protocol Headers
printf '\0prompt\x1fAnimations\n'
printf '\0markup-rows\x1ftrue\n'
printf '\0no-custom\x1ftrue\n'
printf '\0message\x1fSelect a configuration to apply instantly\n'

# Validate Source Directory
if [[ ! -d "$ANIM_DIR" ]]; then
    printf '%s\0icon\x1f%s\x1finfo\x1fignore\n' "Directory Missing" "$ICON_ERROR"
    exit 0
fi

# Gather .lua files safely
shopt -s nullglob
files=("$ANIM_DIR"/*.lua)
shopt -u nullglob

if [[ ${#files[@]} -eq 0 ]]; then
    printf '%s\0icon\x1f%s\x1finfo\x1fignore\n' "No .lua files found" "$ICON_ERROR"
    exit 0
fi

# Determine Active File via Content Comparison
active_index=-1

if [[ -f "$DEST_FILE" ]]; then
    for i in "${!files[@]}"; do
        if cmp -s "${files[$i]}" "$DEST_FILE"; then
            active_index=$i
            break
        fi
    done
fi

# Tell Rofi which row to highlight
if (( active_index >= 0 )); then
    printf '\0active\x1f%d\n' "$active_index"
fi

# Generate Rows
for i in "${!files[@]}"; do
    file="${files[$i]}"
    filename="${file##*/}"
    
    escaped_name=$(escape_markup "$filename")

    if (( i == active_index )); then
        printf "<span weight='bold'>%s</span> <span size='small' style='italic'>(Active)</span>\0icon\x1f%s\x1finfo\x1f%s\n" \
            "$escaped_name" "$ICON_ACTIVE" "$file"
    else
        printf '%s\0icon\x1f%s\x1finfo\x1f%s\n' \
            "$escaped_name" "$ICON_FILE" "$file"
    fi
done

exit 0
