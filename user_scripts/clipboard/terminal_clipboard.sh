#!/usr/bin/env bash
#==============================================================================
# FZF Clipboard Manager with Live Image Preview
# For Arch Linux / Hyprland
#==============================================================================

set -o nounset
set -o pipefail
shopt -s nullglob extglob

#==============================================================================
# CONFIGURATION
#==============================================================================
readonly XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
readonly XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"

# --- UWSM / PERSISTENCE INTEGRATION ---
# COMPATIBILITY RESTORED: Uses 'eval' to correctly expand variables (like
# ${XDG_RUNTIME_DIR}) found in the uwsm/env file.
if [[ -z "${CLIPHIST_DB_PATH:-}" ]] && [[ -f "$HOME/.config/uwsm/env" ]]; then
    # Extract only the active export line for the DB path
    db_config=$(grep -E '^export CLIPHIST_DB_PATH=' "$HOME/.config/uwsm/env" || true)
    if [[ -n "$db_config" ]]; then
        eval "$db_config"
    fi
fi

# Compatible with original rofi script
readonly PINS_DIR="$XDG_DATA_HOME/rofi-cliphist/pins"
readonly CACHE_DIR="$XDG_CACHE_HOME/rofi-cliphist/images"

# Display
readonly MAX_PREVIEW_LEN=60

# Separator (Unit Separator ASCII 0x1F - won't appear in normal text)
readonly SEP=$'\x1f'

# Icons - Using emoji for universal compatibility
readonly ICON_PIN="📌"
readonly ICON_IMG="📸"

# Self reference
readonly SELF="$(realpath "${BASH_SOURCE[0]}")"

#==============================================================================
# HELPERS
#==============================================================================
notify() {
    local msg="$1" urgency="${2:-normal}"
    if command -v notify-send &>/dev/null; then
        notify-send -u "$urgency" -a "Clipboard" "📋 Clipboard" "$msg" 2>/dev/null
    fi
    [[ "$urgency" == "critical" ]] && printf '\e[31mError:\e[0m %s\n' "$msg" >&2
}

check_deps() {
    local missing=()
    for cmd in fzf cliphist wl-copy; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    if ((${#missing[@]})); then
        notify "Missing: ${missing[*]}\n\nInstall:\nsudo pacman -S fzf wl-clipboard\nparu -S cliphist" "critical"
        exit 1
    fi
    
    # Warn about optional deps (once)
    local warn_flag="$CACHE_DIR/.warned"
    if [[ ! -f "$warn_flag" ]]; then
        mkdir -p "$CACHE_DIR"
        local opt=()
        command -v chafa &>/dev/null || opt+=("chafa")
        command -v bat &>/dev/null || opt+=("bat")
        ((${#opt[@]})) && notify "Recommended: sudo pacman -S ${opt[*]}" "low"
        : > "$warn_flag" 2>/dev/null
    fi
}

setup_dirs() {
    mkdir -p "$PINS_DIR" "$CACHE_DIR" 2>/dev/null
    chmod 700 "$PINS_DIR" "$CACHE_DIR" 2>/dev/null
}

#==============================================================================
# UTILITIES
#==============================================================================
generate_hash() {
    local hash
    if command -v b2sum &>/dev/null; then
        hash=$(printf '%s' "$1" | b2sum)
    else
        hash=$(printf '%s' "$1" | md5sum)
    fi
    # Optimization: Use Bash substring instead of 'cut'
    printf '%s' "${hash:0:16}"
}

sanitize_text() {
    local text="$1" max="${2:-$MAX_PREVIEW_LEN}"
    text="${text//[$'\n\r\t\v\f\x00'-$'\x1f']/ }"
    while [[ "$text" == *"  "* ]]; do text="${text//  / }"; done
    text="${text#"${text%%[![:space:]]*}"}"
    text="${text%"${text##*[![:space:]]}"}"
    text="${text//$SEP/}"
    ((${#text} > max)) && text="${text:0:max}…"
    printf '%s' "${text:-[empty]}"
}

#==============================================================================
# IMAGE DETECTION
#==============================================================================
is_image() {
    local lower="${1,,}"
    if [[ "$lower" == *"binary"* ]]; then
        [[ "$lower" == *"png"* ]] && return 0
        [[ "$lower" == *"jpg"* ]] && return 0
        [[ "$lower" == *"jpeg"* ]] && return 0
        [[ "$lower" == *"gif"* ]] && return 0
        [[ "$lower" == *"webp"* ]] && return 0
        [[ "$lower" == *"bmp"* ]] && return 0
        [[ "$lower" == *"tiff"* ]] && return 0
        [[ "$lower" == *"image/"* ]] && return 0
    fi
    return 1
}

parse_image_info() {
    local content="$1"
    local dims="" fmt=""
    
    if [[ "$content" =~ ([0-9]+)[xX]([0-9]+) ]]; then
        dims="${BASH_REMATCH[1]}×${BASH_REMATCH[2]}"
    fi
    
    local lower="${content,,}"
    if [[ "$lower" == *"png"* ]]; then fmt="PNG"
    elif [[ "$lower" == *"jpeg"* ]] || [[ "$lower" == *"jpg"* ]]; then fmt="JPG"
    elif [[ "$lower" == *"gif"* ]]; then fmt="GIF"
    elif [[ "$lower" == *"webp"* ]]; then fmt="WebP"
    elif [[ "$lower" == *"bmp"* ]]; then fmt="BMP"
    fi
    
    if [[ -n "$dims" && -n "$fmt" ]]; then
        printf '%s %s' "$dims" "$fmt"
    elif [[ -n "$dims" ]]; then
        printf '%s' "$dims"
    elif [[ -n "$fmt" ]]; then
        printf '%s' "$fmt"
    else
        printf '[Image]'
    fi
}

#==============================================================================
# IMAGE CACHING & DISPLAY
#==============================================================================
cache_image() {
    local id="$1"
    local path="$CACHE_DIR/${id}.png"
    
    [[ -f "$path" ]] && { printf '%s' "$path"; return 0; }
    
    # Improved Hygiene: mktemp + trap
    local tmp
    tmp=$(mktemp "$CACHE_DIR/${id}.tmp.XXXXXX") || return 1
    trap 'rm -f "$tmp" 2>/dev/null' RETURN
    
    if cliphist decode "$id" > "$tmp" 2>/dev/null; then
        local ftype
        ftype=$(file -b "$tmp" 2>/dev/null)
        if [[ "${ftype,,}" == *"image"* ]] || [[ "${ftype,,}" == *"bitmap"* ]] || \
           [[ "${ftype,,}" == *"png"* ]] || [[ "${ftype,,}" == *"jpeg"* ]] || \
           [[ "${ftype,,}" == *"gif"* ]] || [[ "${ftype,,}" == *"webp"* ]]; then
            mv -f "$tmp" "$path" 2>/dev/null
            trap - RETURN
            printf '%s' "$path"
            return 0
        fi
    fi
    return 1
}

is_kitty() {
    [[ -n "${KITTY_PID:-}" ]] || [[ "${TERM:-}" == *kitty* ]] || [[ -n "${KITTY_WINDOW_ID:-}" ]]
}

kitty_clear() {
    printf '\e_Ga=d,d=A\e\\'
}

display_image() {
    local img="$1"
    local cols="${FZF_PREVIEW_COLUMNS:-40}"
    local rows="${FZF_PREVIEW_LINES:-20}"
    
    [[ ! -f "$img" ]] && { printf '\e[31mImage not found\e[0m\n'; return 1; }
    
    ((rows > 4)) && ((rows -= 3))
    
    if is_kitty && command -v kitten &>/dev/null; then
        kitten icat --clear --transfer-mode=memory --stdin=no \
                    --place="${cols}x${rows}@0x1" "$img" 2>/dev/null
    elif command -v chafa &>/dev/null; then
        chafa --size="${cols}x${rows}" --animate=off "$img" 2>/dev/null
    else
        printf '\e[33mInstall chafa or use Kitty for image preview\e[0m\n'
    fi
}

#==============================================================================
# LIST GENERATION
#==============================================================================
cmd_list() {
    local n=0
    
    # === PINNED ITEMS ===
    local pin hash content preview
    while IFS= read -r pin; do
        [[ -r "$pin" ]] || continue
        ((n++))
        hash="${pin##*/}"; hash="${hash%.pin}"
        content=$(<"$pin") || continue
        preview=$(sanitize_text "$content" 55)
        printf '%d %s %s%s%s%s%s\n' "$n" "$ICON_PIN" "$preview" "$SEP" "pin" "$SEP" "$hash"
    done < <(
        find "$PINS_DIR" -maxdepth 1 -name '*.pin' -type f -printf '%T@\t%p\n' 2>/dev/null \
        | sort -rn | cut -f2
    )
    
    # === HISTORY ITEMS ===
    local line id content
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        ((n++))
        
        id="${line%%$'\t'*}"
        content="${line#*$'\t'}"
        
        if is_image "$content"; then
            local info
            info=$(parse_image_info "$content")
            printf '%d %s %s%s%s%s%s\n' "$n" "$ICON_IMG" "$info" "$SEP" "img" "$SEP" "$id"
        else
            local preview
            preview=$(sanitize_text "$content" 55)
            printf '%d %s%s%s%s%s\n' "$n" "$preview" "$SEP" "txt" "$SEP" "$id"
        fi
    done < <(cliphist list 2>/dev/null)
    
    ((n == 0)) && printf '  (clipboard empty)%s%s%s\n' "$SEP" "empty" "$SEP" ""
}

#==============================================================================
# PREVIEW
#==============================================================================
cmd_preview() {
    local input="$1"
    is_kitty && kitty_clear
    
    [[ "$input" == *"(clipboard empty)"* ]] && {
        printf '\n\e[90mClipboard is empty.\nCopy something to get started!\e[0m\n'
        return 0
    }
    
    local visible type id
    IFS="$SEP" read -r visible type id <<< "$input"
    
    case "$type" in
        pin)
            printf '\e[1;33m━━━ %s PINNED ━━━\e[0m\n\n' "$ICON_PIN"
            local pin_file="$PINS_DIR/${id}.pin"
            if [[ -f "$pin_file" ]]; then
                if command -v bat &>/dev/null; then
                    bat --style=plain --color=always --paging=never "$pin_file" 2>/dev/null
                else
                    cat "$pin_file"
                fi
            else
                printf '\e[31mPin not found\e[0m\n'
            fi
            ;;
        
        img)
            printf '\e[1;36m━━━ %s IMAGE ━━━\e[0m\n' "$ICON_IMG"
            local img_path
            if img_path=$(cache_image "$id"); then
                file -b "$img_path" 2>/dev/null | head -c 50
                printf '\n\n'
                display_image "$img_path"
            else
                printf '\n\e[31mFailed to decode image\e[0m\n'
            fi
            ;;
        
        txt)
            printf '\e[1;32m━━━ TEXT ━━━\e[0m\n\n'
            local content
            if content=$(cliphist decode "$id" 2>/dev/null); then
                if ((${#content} > 50000)); then
                    printf '%s' "${content:0:50000}"
                    printf '\n\n\e[90m[...truncated...]\e[0m\n'
                else
                    printf '%s' "$content"
                fi
            else
                printf '\e[31mFailed to decode\e[0m\n'
            fi
            ;;
        
        empty)
            printf '\e[90mNothing here\e[0m\n'
            ;;
        
        *)
            printf '\e[31mUnknown type: %s\e[0m\n' "$type"
            printf 'Raw input: %s\n' "$input"
            ;;
    esac
}

#==============================================================================
# ACTIONS
#==============================================================================
cmd_copy() {
    local input="$1" visible type id
    IFS="$SEP" read -r visible type id <<< "$input"
    
    case "$type" in
        pin)
            [[ -f "$PINS_DIR/${id}.pin" ]] && wl-copy < "$PINS_DIR/${id}.pin"
            ;;
        img)
            cliphist decode "$id" 2>/dev/null | wl-copy --type image/png
            ;;
        txt)
            cliphist decode "$id" 2>/dev/null | wl-copy
            ;;
    esac
}

cmd_pin() {
    local input="$1" visible type id
    IFS="$SEP" read -r visible type id <<< "$input"
    
    case "$type" in
        pin)
            rm -f "$PINS_DIR/${id}.pin"
            ;;
        img)
            notify "Image pinning not supported" "low"
            ;;
        txt)
            local content hash pin_file tmp_file
            if content=$(cliphist decode "$id" 2>/dev/null) && [[ -n "$content" ]]; then
                hash=$(generate_hash "$content")
                pin_file="$PINS_DIR/${hash}.pin"
                
                # Atomic write: secure temp file, chmod, then move
                tmp_file="${pin_file}.tmp.$$"
                printf '%s' "$content" > "$tmp_file"
                chmod 600 "$tmp_file"
                mv -f "$tmp_file" "$pin_file"
            fi
            ;;
    esac
}

cmd_delete() {
    local input="$1" visible type id
    IFS="$SEP" read -r visible type id <<< "$input"
    
    case "$type" in
        pin) rm -f "$PINS_DIR/${id}.pin" ;;
        img) cliphist delete "$id" 2>/dev/null; rm -f "$CACHE_DIR/${id}.png" ;;
        txt) cliphist delete "$id" 2>/dev/null ;;
    esac
}

cmd_wipe() {
    cliphist wipe 2>/dev/null
    rm -f "$CACHE_DIR"/*.png "$CACHE_DIR"/*.tmp.* 2>/dev/null
}

#==============================================================================
# MAIN MENU
#==============================================================================
show_menu() {
    if [[ ! -t 0 ]] || [[ ! -t 1 ]]; then
        if command -v kitty &>/dev/null; then
            exec kitty --class=cliphist-fzf --title="Clipboard" \
                -o remember_window_size=no \
                -o initial_window_width=95c \
                -o initial_window_height=20c \
                -o confirm_os_window_close=0 \
                -e "$SELF"
        elif command -v foot &>/dev/null; then
            exec foot --app-id=cliphist-fzf --title="Clipboard" \
                --window-size-chars=95x20 "$SELF"
        elif command -v alacritty &>/dev/null; then
            exec alacritty --class=cliphist-fzf --title="Clipboard" \
                -o window.dimensions.columns=95 \
                -o window.dimensions.lines=20 \
                -e "$SELF"
        else
            notify "No terminal found. Install kitty, foot, or alacritty" "critical"
            exit 1
        fi
    fi
    
    trap 'is_kitty && kitty_clear' EXIT INT TERM
    
    local selection
    selection=$(cmd_list | fzf \
        --ansi \
        --reverse \
        --no-sort \
        --exact \
        --no-multi \
        --cycle \
        --margin=0 \
        --padding=0 \
        --border=rounded \
        --border-label=" 📋 Clipboard " \
        --border-label-pos=3 \
        --info=hidden \
        --header="Alt+ (t=Wipe u=Pin y=Unpin)" \
        --header-first \
        --prompt="  " \
        --pointer="▌" \
        --delimiter="$SEP" \
        --with-nth=1 \
        --preview="'$SELF' --preview {}" \
        --preview-window="right,45%,~1,wrap" \
        --bind="enter:accept" \
        --bind="alt-u:execute-silent('$SELF' --pin {})+reload('$SELF' --list)" \
        --bind="alt-y:execute-silent('$SELF' --delete {})+reload('$SELF' --list)" \
        --bind="alt-t:execute-silent('$SELF' --wipe)+reload('$SELF' --list)" \
        --bind="esc:abort" \
        --bind="ctrl-c:abort"
    )
    
    is_kitty && kitty_clear
    
    if [[ -n "$selection" ]]; then
        cmd_copy "$selection"
    fi
    
    # Forcefully kill the parent process (the terminal) to ensure everything closes.
    kill -9 $PPID
}

#==============================================================================
# ENTRY POINT
#==============================================================================
main() {
    case "${1:-}" in
        --list)    cmd_list ;;
        --preview) shift; cmd_preview "$*" ;;
        --pin)     shift; cmd_pin "$*" ;;
        --delete)  shift; cmd_delete "$*" ;;
        --wipe)    cmd_wipe ;;
        --help|-h)
            cat <<'EOF'
FZF Clipboard Manager - Live Image Preview

USAGE:
    clipboard-manager           Launch
    clipboard-manager --help    This help

KEYS:
    Enter    Copy to clipboard
    Alt-u    Pin / Unpin
    Alt-y    Delete item
    Alt-t    Wipe history (keeps pins)
    Esc      Exit

DEPS:
    Required: fzf cliphist wl-clipboard
    Optional: chafa bat kitty
EOF
            ;;
        *)
            check_deps
            setup_dirs
            show_menu
            ;;
    esac
}

main "$@"
