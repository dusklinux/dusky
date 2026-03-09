#!/usr/bin/env bash
#==============================================================================
# FZF CLIPBOARD MANAGER
# Role: Arch Linux / Hyprland / UWSM Clipboard Utility
# Description: High-performance, secure clipboard manager with image previews
# Dependencies: fzf, cliphist, wl-copy, (optional: chafa, bat, kitty)
#==============================================================================
# NOTE: `set -o errexit` is intentionally OMITTED. Functions rely on
# `return 1` for control flow (cache miss, non-image entry, etc.).
#==============================================================================

#
#        CHANGE THIS LINE FOR CUSTOOMIZING THE UI , IT'S ON LINE 520
#         --preview="'$SELF' --preview {}" --preview-window="right,45%,~1,wrap" \

set -o nounset
set -o pipefail
shopt -s nullglob extglob

#==============================================================================
# CONFIGURATION
#==============================================================================
readonly XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
readonly XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"

# --- UWSM / Persistence Integration ---
# Robustly parse ~/.config/uwsm/env to find CLIPHIST_DB_PATH
if [[ -z "${CLIPHIST_DB_PATH:-}" ]]; then
    # GUARDRAIL: Only parse the UWSM env file if the user is ACTUALLY running UWSM.
    if [[ "${DESKTOP_SESSION:-}" == *"uwsm"* || -n "${UWSM_FINALIZE_VARNAMES:-}" ]]; then
        _uwsm_env="$HOME/.config/uwsm/env"
        if [[ -f "$_uwsm_env" ]]; then
            # '|| [[ -n... ]]' prevents silent drops if the file lacks a trailing newline
            while IFS= read -r _raw_line || [[ -n "$_raw_line" ]]; do
                # Left-trim whitespace (utilizing extglob enabled at the top of the script)
                _line="${_raw_line##+([[:space:]])}"
                
                # Skip empty lines and comments safely
                [[ -z "$_line" || "$_line" == "#"* ]] && continue
                
                # Strip optional 'export ' prefix and subsequent spaces to catch bare assignments
                _line="${_line#export }"
                _line="${_line##+([[:space:]])}"
                
                # Match target variable
                if [[ "$_line" == "CLIPHIST_DB_PATH="* ]]; then
                    _raw_val="${_line#CLIPHIST_DB_PATH=}"
                    
                    # Strip surrounding quotes
                    if [[ "$_raw_val" =~ ^\"(.*)\"$ || "$_raw_val" =~ ^\'(.*)\'$ ]]; then
                        _raw_val="${BASH_REMATCH[1]}"
                    fi
                    
                    # Expand variables securely (no eval)
                    # Braced forms (always unambiguous)
                    _raw_val="${_raw_val//\$\{XDG_RUNTIME_DIR\}/${XDG_RUNTIME_DIR:-}}"
                    _raw_val="${_raw_val//\$\{HOME\}/${HOME}}"
                    
                    # Unbraced forms (append '/' to prevent greedy matches on longer variable names)
                    _raw_val="${_raw_val//\$XDG_RUNTIME_DIR\//${XDG_RUNTIME_DIR:-}/}"
                    _raw_val="${_raw_val//\$HOME\//${HOME}/}"
                    
                    export CLIPHIST_DB_PATH="$_raw_val"
                    break
                fi
            done < "$_uwsm_env"
        fi
        unset _uwsm_env _raw_line _line _raw_val
    fi
    # NOTE: The non-UWSM fallback warning was intentionally removed here to prevent log spam 
    # for users utilizing standard Hyprland sessions.
fi

readonly PINS_DIR="$XDG_DATA_HOME/rofi-cliphist/pins"
readonly CACHE_DIR="$XDG_CACHE_HOME/rofi-cliphist/images"

# Separator (Unit Separator ASCII 0x1F)
readonly SEP=$'\x1f'

# Icons
readonly ICON_PIN="📌"
readonly ICON_IMG="📸"

# Self reference
readonly SELF="$(realpath "${BASH_SOURCE[0]}")"

# Hash command detection (done once)
if command -v b2sum &>/dev/null; then
    readonly _HASH_CMD="b2sum"
else
    readonly _HASH_CMD="md5sum"
fi

# --- Global Temp File Tracking (Template Pattern) ---
declare _TMPFILE=""

# --- Invocation Mode Detection ---
# Determined once at startup so cleanup knows whether kitty_clear is safe.
# Preview subprocesses must NOT clear kitty images on exit.
readonly _INVOCATION_MODE="${1:-__main__}"

#==============================================================================
# SYSTEM HELPERS
#==============================================================================
log_err() {
    printf '\e[31m[ERROR]\e[0m %s\n' "$1" >&2
}

cleanup() {
    # Secure temp file cleanup (template pattern)
    if [[ -n "${_TMPFILE:-}" && -f "$_TMPFILE" ]]; then
        rm -f "$_TMPFILE" 2>/dev/null || :
    fi
    # Kitty image protocol cleanup — ONLY for the main interactive session.
    # Preview subprocesses (--preview) must NOT clear, or they destroy
    # the image they just rendered before fzf can display it.
    if [[ "$_INVOCATION_MODE" == "__main__" ]]; then
        is_kitty && kitty_clear 2>/dev/null || :
    fi
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

notify() {
    local msg="$1" urgency="${2:-normal}"
    if command -v notify-send &>/dev/null; then
        notify-send -u "$urgency" -a "Clipboard" "📋 Clipboard" "$msg" 2>/dev/null
    fi
    [[ "$urgency" == "critical" ]] && log_err "$msg"
}

check_deps() {
    local cmd missing=()
    for cmd in fzf cliphist wl-copy; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    if ((${#missing[@]})); then
        notify "Missing: ${missing[*]}\nInstall: sudo pacman -S fzf wl-clipboard cliphist" "critical"
        exit 1
    fi

    # Warn about optional deps once
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
    if [[ ! -d "$PINS_DIR" ]] || [[ ! -d "$CACHE_DIR" ]]; then
        mkdir -p "$PINS_DIR" "$CACHE_DIR" 2>/dev/null
        chmod 700 "$PINS_DIR" "$CACHE_DIR" 2>/dev/null
    fi
}

generate_hash() {
    local hash_line hash
    hash_line=$(printf '%s' "$1" | "$_HASH_CMD")
    # Clean split to handle "hash  filename" output safely
    hash="${hash_line%% *}"
    printf '%s' "${hash:0:16}"
}

#==============================================================================
# IMAGE HANDLING
#==============================================================================
# Detects actual image files (file utility output)
_file_is_image() {
    local lower="${1,,}"
    case "$lower" in
        *image*|*bitmap*|*png*|*jpeg*|*gif*|*webp*|*bmp*|*tiff*) return 0 ;;
    esac
    return 1
}

cache_image() {
    local id="$1"

    # SECURITY: Prevent path traversal
    [[ "$id" =~ ^[0-9]+$ ]] || return 1

    local path="${CACHE_DIR}/${id}.png"

    # Cache hit check (reject symlinks)
    if [[ -f "$path" && ! -L "$path" ]]; then
        printf '%s' "$path"
        return 0
    fi

    # Atomic creation with global tracking (template pattern)
    _TMPFILE=$(mktemp "${CACHE_DIR}/tmp.XXXXXX") || return 1

    # FIX: Use pipe with TAB to match cliphist expectation
    if printf '%s\t\n' "$id" | cliphist decode > "$_TMPFILE" 2>/dev/null; then
        local ftype
        ftype=$(file -b -- "$_TMPFILE" 2>/dev/null)
        if _file_is_image "${ftype:-}"; then
            if mv -f "$_TMPFILE" "$path" 2>/dev/null; then
                _TMPFILE=""
                printf '%s' "$path"
                return 0
            fi
        fi
    fi

    # Cleanup on failure
    rm -f "$_TMPFILE" 2>/dev/null || :
    _TMPFILE=""
    return 1
}

is_kitty() {
    [[ -n "${KITTY_PID:-}${KITTY_WINDOW_ID:-}" || "${TERM:-}" == *kitty* ]]
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
# CORE LOGIC: LIST GENERATION
#==============================================================================
cmd_list() {
    local n=0

    # --- Pinned Items ---
    local pin hash content preview
    while IFS= read -r pin; do
        [[ -r "$pin" ]] || continue
        ((n++))

        hash="${pin##*/}"
        hash="${hash%.pin}"

        # Zero-fork memory protection: Read max 512 bytes natively
        content=""
        read -r -d '' -n 512 content < "$pin" || true
        [[ -z "$content" ]] && continue

        # Inline sanitization
        preview="${content//$'\n'/ }"
        preview="${preview//$'\r'/}"
        preview="${preview//$'\t'/ }"
        preview="${preview//"$SEP"/ }"
        if ((${#preview} > 55)); then preview="${preview:0:55}…"; fi

        printf '%d %s %s%s%s%s%s\n' "$n" "$ICON_PIN" "$preview" "$SEP" "pin" "$SEP" "$hash"
    done < <(find "${PINS_DIR:?}" -maxdepth 1 -name '*.pin' -type f -printf '%T@\t%p\n' 2>/dev/null | sort -rn | cut -f2)

    # --- History Items (Zero-Fork Pipeline) ---
    # OPTIMIZATION: LC_ALL=C ensures 'tolower' works correctly on non-English locales (Template Compliance)
    cliphist list 2>/dev/null | LC_ALL=C awk \
        -v pin_count="$n" \
        -v icon_img="$ICON_IMG" \
        -v sep="$SEP" \
        -v max_len=55 \
    '
    BEGIN { FS = "\t"; n = 0 }

    /^[[:space:]]*$/ { next }

    {
        id = $1
        content = ""
        for (i = 2; i <= NF; i++) content = (i == 2) ? $i : (content "\t" $i)

        n++
        idx = n + pin_count

        # User output is: [[ binary data ... ]] (Space exists)
        if (content ~ /^\[\[ *binary data/) {

            # 1. Extract Dimensions (e.g. 1091x430)
            dims = ""
            if (match(content, /[0-9]+[xX][0-9]+/)) {
                dims = substr(content, RSTART, RLENGTH)
                gsub(/[xX]/, "×", dims)
            }

            # 2. Extract Format
            fmt = ""
            lc = tolower(content)
            if (index(lc, "png")) fmt = "PNG"
            else if (index(lc, "jpeg") || index(lc, "jpg")) fmt = "JPG"
            else if (index(lc, "gif")) fmt = "GIF"
            else if (index(lc, "webp")) fmt = "WebP"
            else if (index(lc, "bmp")) fmt = "BMP"
            else if (index(lc, "tiff")) fmt = "TIFF"

            # 3. Construct Display String
            info = ""
            if (dims != "" && fmt != "") info = dims " " fmt
            else if (dims != "") info = dims
            else if (fmt != "") info = fmt
            else info = "[Image]"

            printf "%d %s %s%s%s%s%s\n", idx, icon_img, info, sep, "img", sep, id
        } else {
            # Text Entry
            gsub(/[[:cntrl:]]/, " ", content)
            gsub(/  +/, " ", content)
            gsub(/^ +| +$/, "", content)
            gsub(sep, " ", content) # Strip separator

            if (length(content) > max_len) content = substr(content, 1, max_len) "…"
            printf "%d %s%s%s%s%s\n", idx, content, sep, "txt", sep, id
        }
    }

    END {
        if (n == 0 && pin_count == 0) {
            printf "  (clipboard empty)%s%s%s\n", sep, "empty", sep
        }
    }
    '
}

#==============================================================================
# PREVIEW LOGIC
#==============================================================================
cmd_preview() {
    local input="$1"

    # Clear previous kitty image BEFORE rendering new one (not on exit)
    is_kitty && kitty_clear

    [[ -z "$input" ]] && { printf '\e[90mNo selection.\e[0m\n'; return 0; }
    [[ "$input" == *"(clipboard empty)"* ]] && {
        printf '\n\e[90mClipboard is empty.\nCopy something to get started!\e[0m\n'; return 0;
    }

    # Right-to-Left Parsing for safety
    local type id rest
    id="${input##*"${SEP}"}"
    rest="${input%"${SEP}"*}"
    type="${rest##*"${SEP}"}"

    case "$type" in
        pin)
            printf '\e[1;33m━━━ %s PINNED ━━━\e[0m\n\n' "$ICON_PIN"
            local pin_file="${PINS_DIR:?}/${id}.pin"
            if [[ -f "$pin_file" ]]; then
                if command -v bat &>/dev/null; then
                    bat --style=plain --color=always --paging=never --wrap=character \
                        --terminal-width="${FZF_PREVIEW_COLUMNS:-80}" "$pin_file" 2>/dev/null \
                    || cat -- "$pin_file"
                else
                    cat -- "$pin_file"
                fi
            else
                printf '\e[31mPin file missing.\e[0m\n'
            fi
            ;;
        img)
            printf '\e[1;36m━━━ %s IMAGE ━━━\e[0m\n' "$ICON_IMG"
            local img_path
            if img_path=$(cache_image "$id") && [[ -f "$img_path" ]]; then
                file -b -- "$img_path" 2>/dev/null | head -c 80
                printf '\n\n'
                display_image "$img_path"
            else
                printf '\n\e[31mFailed to decode image.\e[0m\n'
            fi
            ;;
        txt)
            printf '\e[1;32m━━━ TEXT ━━━\e[0m\n\n'
            local content
            # FIX: Included \t to ensure cliphist parses the ID correctly
            if content=$(printf '%s\t\n' "$id" | cliphist decode 2>/dev/null) && [[ -n "$content" ]]; then
                if ((${#content} > 50000)); then
                    printf '%s' "${content:0:50000}"
                    printf '\n\n\e[90m[...truncated...]\e[0m\n'
                else
                    printf '%s' "$content"
                fi
            else
                printf '\e[31mFailed to decode entry.\e[0m\n'
            fi
            ;;
        *)
            printf '\e[31mUnknown type: %q\e[0m\n' "$type"
            ;;
    esac
}

#==============================================================================
# ACTIONS
#==============================================================================
cmd_copy() {
    local input="$1" visible type id
    IFS="$SEP" read -r visible type id <<< "$input"
    [[ -z "${type:-}" || -z "${id:-}" ]] && return 1

    case "$type" in
        pin)
            [[ -f "$PINS_DIR/${id}.pin" ]] && wl-copy < "$PINS_DIR/${id}.pin"
            ;;
        img)
            # FIX: Included \t to ensure cliphist parses the ID correctly
            printf '%s\t\n' "$id" | cliphist decode 2>/dev/null | wl-copy --type "image/png"
            ;;
        txt)
            # FIX: Included \t to ensure cliphist parses the ID correctly
            printf '%s\t\n' "$id" | cliphist decode 2>/dev/null | wl-copy
            ;;
    esac
}

cmd_pin() {
    local input="$1" visible type id
    IFS="$SEP" read -r visible type id <<< "$input"
    [[ -z "${type:-}" || -z "${id:-}" ]] && return 1

    case "$type" in
        pin)
            rm -f "$PINS_DIR/${id}.pin"
            ;;
        txt)
            local content hash pin_file
            # FIX: Included \t to ensure cliphist parses the ID correctly
            content=$(printf '%s\t\n' "$id" | cliphist decode 2>/dev/null) || return 1
            [[ -z "$content" ]] && return 1

            hash=$(generate_hash "$content")
            pin_file="$PINS_DIR/${hash}.pin"

            # Atomic write with global tracking (template pattern)
            # CRITICAL: Use cat > target to preserve any symlinks (template pattern)
            _TMPFILE=$(mktemp "${PINS_DIR}/.pin.XXXXXX") || return 1

            if printf '%s' "$content" > "$_TMPFILE"; then
                cat "$_TMPFILE" > "$pin_file"
                rm -f "$_TMPFILE" 2>/dev/null || :
                _TMPFILE=""
            else
                rm -f "$_TMPFILE" 2>/dev/null || :
                _TMPFILE=""
                return 1
            fi
            ;;
    esac
}

cmd_delete() {
    local input="$1" visible type id
    IFS="$SEP" read -r visible type id <<< "$input"
    [[ -z "${type:-}" || -z "${id:-}" ]] && return 1

    case "$type" in
        pin) rm -f "$PINS_DIR/${id}.pin" ;;
        img)
            # FIX: Included \t to ensure cliphist parses the ID correctly
            printf '%s\t\n' "$id" | cliphist delete 2>/dev/null
            rm -f "$CACHE_DIR/${id}.png"
            ;;
        txt)
            # FIX: Included \t to ensure cliphist parses the ID correctly
            printf '%s\t\n' "$id" | cliphist delete 2>/dev/null
            ;;
    esac
}

cmd_wipe() {
    cliphist wipe 2>/dev/null
    rm -f "$CACHE_DIR"/*.png 2>/dev/null
    rm -f "$PINS_DIR"/.pin.?????? 2>/dev/null
}

#==============================================================================
# UI & ENTRY POINT
#==============================================================================
show_menu() {
    if [[ ! -t 0 ]] || [[ ! -t 1 ]]; then
        local term_cmd=()
        if command -v kitty &>/dev/null; then
            term_cmd=(kitty --class=cliphist-fzf --title="Clipboard" -o confirm_os_window_close=0 -e "$SELF")
        elif command -v foot &>/dev/null; then
            term_cmd=(foot --app-id=cliphist-fzf --title="Clipboard" --window-size-chars=95x20 "$SELF")
        elif command -v alacritty &>/dev/null; then
            term_cmd=(alacritty --class=cliphist-fzf --title="Clipboard" -o window.dimensions.columns=95 -o window.dimensions.lines=20 -e "$SELF")
        else
            notify "No terminal found." "critical"; exit 1
        fi
        exec "${term_cmd[@]}"
    fi

    local selection
    selection=$(cmd_list | fzf \
        --ansi --reverse --no-sort --exact --no-multi --cycle \
        --margin=0 --padding=0 \
        --border=rounded --border-label=" 📋 Clipboard " --border-label-pos=3 \
        --info=hidden --header="Alt+ (t=Wipe u=Pin/Unpin y=Delete)" --header-first \
        --prompt="  " --pointer="▌" --delimiter="$SEP" --with-nth=1 \
        --preview="'$SELF' --preview {}" --preview-window="right,45%,~1,wrap" \
        --bind="enter:accept" \
        --bind="alt-u:execute-silent('$SELF' --pin {})+reload('$SELF' --list)" \
        --bind="alt-y:execute-silent('$SELF' --delete {})+reload('$SELF' --list)" \
        --bind="alt-t:execute-silent('$SELF' --wipe)+reload('$SELF' --list)" \
        --bind="esc:abort" --bind="ctrl-c:abort"
    ) || true

    if [[ -n "$selection" ]]; then
        cmd_copy "$selection"
    fi

    # SAFETY: Only kill the parent process if we are reasonably sure it is
    # the ephemeral kitty window we spawned.
    if [[ -n "${KITTY_PID:-}" || "${TERM:-}" == *kitty* ]]; then
        sleep 0.10  # Allow wl-copy time to handshake with Wayland
        kill -15 $PPID 2>/dev/null || :
    fi
}

main() {
    # Pre-flight: Bash version guard (template pattern)
    if (( BASH_VERSINFO[0] < 5 )); then
        log_err "Bash 5.0+ required (found ${BASH_VERSION})"; exit 1
    fi

    case "${1:-}" in
        --list)    cmd_list ;;
        --preview) [[ $# -ge 2 ]] && { shift; cmd_preview "$1"; } ;;
        --pin)     [[ $# -ge 2 ]] && { shift; cmd_pin "$1"; } ;;
        --delete)  [[ $# -ge 2 ]] && { shift; cmd_delete "$1"; } ;;
        --wipe)    cmd_wipe ;;
        --help|-h)
            printf 'Usage: clipboard-manager [launch|--help]\n'
            printf 'Dependencies: fzf, cliphist, wl-clipboard\n'
            ;;
        "") check_deps; setup_dirs; show_menu ;;
        *)  log_err "Unknown argument: ${1}"; exit 1 ;;
    esac
}

main "$@"
