#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Hyprland Animation Switcher for Rofi (Bleeding-Edge Edition v3)
# -----------------------------------------------------------------------------
set -euo pipefail

# -----------------------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------------------
readonly CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}"
readonly ANIM_DIR="$CONFIG_DIR/hypr/source/animations"
readonly LINK_DIR="$ANIM_DIR/active"
readonly DEST_FILE="$LINK_DIR/active.lua"
readonly STATE_FILE="$CONFIG_DIR/dusky/settings/dusky_animiation" 
readonly FALLBACK_ANIM="dusky.lua"

# Visual Assets
readonly ICON_ACTIVE=""   
readonly ICON_FILE=""     
readonly ICON_DIR="󰹹"      
readonly ICON_BACK=""     
readonly ICON_ERROR=""    
readonly ICON_DISABLE=""  

# -----------------------------------------------------------------------------
# HELPER FUNCTIONS
# -----------------------------------------------------------------------------
notify_user() {
    local title="$1"
    local message="$2"
    local urgency="${3:-low}"
    # Only surface desktop notifications when invoked from rofi (rofi sets ROFI_INFO on selection).
    # Plain CLI/keybind invocations stay silent.
    [[ -n "${ROFI_INFO:-}" ]] || return 0
    if command -v notify-send &>/dev/null; then
        notify-send -u "$urgency" \
            --app-name=hypr-anim \
            --icon="${4:-applications-graphics}" \
            -h string:x-canonical-private-synchronous:hypr-anim \
            -t 1500 "$title" "$message" &>/dev/null || true
    fi
}

reload_hyprland() {
    if command -v hyprctl &>/dev/null; then
        hyprctl reload config-only &>/dev/null
    fi
}

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
# CORE LOGIC: ATOMIC APPLY
# -----------------------------------------------------------------------------
apply_animation() {
    local target_orient="$1"
    local src_file="$2"

    if [[ ! -f "$src_file" ]]; then
        notify_user "Error" "Target file missing: $src_file" "critical"
        return 1
    fi

    mkdir -p -- "$LINK_DIR" 2>/dev/null

    # V3 CRITICAL FIX: Create tmp file in the SAME directory to guarantee 
    # same-filesystem atomic inode swap, bypassing Arch's tmpfs boundary issue.
    local tmp_file
    tmp_file="$(mktemp "${LINK_DIR}/.active.XXXXXX.tmp")"

    # Capture the quoted path now; function locals may be out of scope at EXIT.
    local cleanup_command
    printf -v cleanup_command 'rm -f -- %q' "$tmp_file"
    trap "$cleanup_command" EXIT

    # V3 CRITICAL FIX: Standardize permissions (mktemp defaults to 0600)
    chmod 644 "$tmp_file"

    if ! awk -v orient="$target_orient" '
    BEGIN { state="normal" }
    /^-- FOR HORIZONTAL/ { state="horiz"; print; next }
    /^-- FOR VERTICAL/   { state="vert"; print; next }
    /^$/ { state="normal" } 
    {
        if (state == "horiz") {
            if (orient == "vertical") {
                if ($0 ~ /^hl\.animation/) sub(/^hl\.animation/, "-- hl.animation")
            } else if (orient == "horizontal") {
                if ($0 ~ /^-- *hl\.animation/) sub(/^-- *hl\.animation/, "hl.animation")
            }
        } else if (state == "vert") {
            if (orient == "vertical") {
                if ($0 ~ /^-- *hl\.animation/) sub(/^-- *hl\.animation/, "hl.animation")
            } else if (orient == "horizontal") {
                if ($0 ~ /^hl\.animation/) sub(/^hl\.animation/, "-- hl.animation")
            }
        }
        print $0
    }
    ' "$src_file" > "$tmp_file"; then
        notify_user "Critical Fault" "Failed to process Lua stream." "critical"
        return 1
    fi

    # Atomic swap: Replaces symlinks natively and cannot be interrupted
    if mv -f -- "$tmp_file" "$DEST_FILE"; then
        trap - EXIT # Disarm the cleanup trap since the move was successful
        
        mkdir -p -- "${STATE_FILE%/*}" 2>/dev/null
        printf '%s|%s\n' "$target_orient" "$src_file" > "$STATE_FILE"

        reload_hyprland
        notify_user "Animation Applied" "${src_file##*/} (${target_orient^})"
        return 0
    else
        notify_user "Filesystem Error" "Failed atomic write to $DEST_FILE" "critical"
        return 1
    fi
}

# -----------------------------------------------------------------------------
# STRICT STATE RETRIEVAL
# -----------------------------------------------------------------------------
get_current_state() {
    current_orient="horizontal" 
    current_anim=""

    if [[ -f "$STATE_FILE" ]]; then
        local saved_state
        saved_state=$(<"$STATE_FILE")
        
        if [[ "$saved_state" == *"|"* ]]; then
            current_orient="${saved_state%|*}"
            current_anim="${saved_state#*|}"
        fi
    fi
}

# -----------------------------------------------------------------------------
# ROUTING & CLI FLAGS
# -----------------------------------------------------------------------------
if [[ "${1:-}" == "--current" ]]; then
    get_current_state
    
    target_anim="$current_anim"
    if [[ -z "$target_anim" || ! -f "$target_anim" ]]; then
        target_anim="$ANIM_DIR/$FALLBACK_ANIM"
    fi

    if [[ -f "$target_anim" ]]; then
        apply_animation "$current_orient" "$target_anim"
        exit 0
    else
        notify_user "Fatal Error" "System fallback animation missing." "critical"
        exit 1
    fi
fi

selection="${ROFI_INFO:-}"

if [[ -z "$selection" && $# -eq 2 && ("$1" == "horizontal" || "$1" == "vertical" || "$1" == "disabled") ]]; then
    selection="FILE:$1:$2"
fi

# -----------------------------------------------------------------------------
# ROFI MENUS
# -----------------------------------------------------------------------------
get_current_state

# STEP 3: Apply Selection
if [[ "$selection" == FILE:* ]]; then
    target_orient="$(echo "$selection" | cut -d':' -f2)"
    target_file="$(echo "$selection" | cut -d':' -f3-)"
    
    apply_animation "$target_orient" "$target_file"
    exit 0
fi

# STEP 2: Show Files
if [[ "$selection" == DIR:* ]]; then
    target_orient="${selection#DIR:}"
    
    printf '\0prompt\x1fAnimations (%s)\n' "${target_orient^}"
    printf '\0markup-rows\x1ftrue\n'
    printf '\0no-custom\x1ftrue\n'
    printf '\0message\x1fSelect a configuration to apply instantly\n'
    
    printf '<span weight="bold">⬅ Back</span>\0icon\x1f%s\x1finfo\x1fBACK\n' "$ICON_BACK"

    shopt -s nullglob
    files=("$ANIM_DIR"/*.lua)
    shopt -u nullglob

    if [[ ${#files[@]} -eq 0 ]]; then
        printf '%s\0icon\x1f%s\x1finfo\x1fignore\n' "No .lua files found in $ANIM_DIR" "$ICON_ERROR"
        exit 0
    fi

    for file in "${files[@]}"; do
        filename="${file##*/}"

        # Prevent the disable.lua file from cluttering the orientation sub-menus
        if [[ "$filename" == "disable.lua" ]]; then
            continue
        fi

        escaped_name=$(escape_markup "$filename")

        if [[ "$file" == "$current_anim" && "$target_orient" == "$current_orient" ]]; then
            printf "<span weight='bold'>%s</span> <span size='small' style='italic'>(Active)</span>\0icon\x1f%s\x1finfo\x1fFILE:%s:%s\n" \
                "$escaped_name" "$ICON_ACTIVE" "$target_orient" "$file"
        else
            printf '%s\0icon\x1f%s\x1finfo\x1fFILE:%s:%s\n' \
                "$escaped_name" "$ICON_FILE" "$target_orient" "$file"
        fi
    done
    exit 0
fi

# STEP 1: Main Menu
if [[ -z "$selection" || "$selection" == "BACK" ]]; then
    printf '\0prompt\x1fOrientation\n'
    printf '\0markup-rows\x1ftrue\n'
    printf '\0no-custom\x1ftrue\n'
    printf '\0message\x1fSelect animation layout orientation\n'

    # Check for disable.lua and list it as the first option if it exists
    if [[ -f "$ANIM_DIR/disable.lua" ]]; then
        if [[ "$current_anim" == "$ANIM_DIR/disable.lua" ]]; then
            printf '<span weight="bold">Disable Animations</span> <span size="small" style="italic">(Active)</span>\0icon\x1f%s\x1finfo\x1fFILE:disabled:%s/disable.lua\n' "$ICON_ACTIVE" "$ANIM_DIR"
        else
            printf 'Disable Animations\0icon\x1f%s\x1finfo\x1fFILE:disabled:%s/disable.lua\n' "$ICON_DISABLE" "$ANIM_DIR"
        fi
    fi

    if [[ "$current_orient" == "horizontal" && -n "$current_anim" && "$current_anim" != "$ANIM_DIR/disable.lua" ]]; then
        printf '<span weight="bold">Horizontal Animations</span> <span size="small" style="italic">(Active)</span>\0icon\x1f%s\x1finfo\x1fDIR:horizontal\n' "$ICON_ACTIVE"
    else
        printf 'Horizontal Animations\0icon\x1f%s\x1finfo\x1fDIR:horizontal\n' "$ICON_DIR"
    fi

    if [[ "$current_orient" == "vertical" && -n "$current_anim" && "$current_anim" != "$ANIM_DIR/disable.lua" ]]; then
        printf '<span weight="bold">Vertical Animations</span> <span size="small" style="italic">(Active)</span>\0icon\x1f%s\x1finfo\x1fDIR:vertical\n' "$ICON_ACTIVE"
    else
        printf 'Vertical Animations\0icon\x1f%s\x1finfo\x1fDIR:vertical\n' "$ICON_DIR"
    fi

    exit 0
fi
