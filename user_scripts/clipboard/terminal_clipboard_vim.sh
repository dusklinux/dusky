#!/usr/bin/env bash
#==============================================================================
# FZF CLIPBOARD MANAGER (v2.3 - Wayland/UWSM Edition)
# Arch Linux / Hyprland / UWSM clipboard utility
# Optimized for: Bash 5.3+, FZF 0.72.0+
#==============================================================================
# NOTE: `set -o errexit` is intentionally omitted. Several functions use
# `return 1` for normal control flow.
#==============================================================================

set -o nounset
set -o pipefail
shopt -s nullglob extglob
umask 077

#==============================================================================
# CONFIGURATION & STATE
#==============================================================================
readonly XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
readonly XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"

# --- User State File (Configurable Settings) ---
readonly USER_STATE_FILE="${HOME}/.config/dusky/settings/clipboard_state"

if [[ ! -f "$USER_STATE_FILE" ]]; then
    mkdir -p -m 700 -- "${USER_STATE_FILE%/*}" 2>/dev/null || :
    cat << 'EOF' > "$USER_STATE_FILE"
# =============================================================================
# CLIPBOARD MANAGER USER SETTINGS
# =============================================================================
# FZF Preview window default layout (e.g., right,45%,~3,wrap-word OR down,50%,~3,wrap-word)
PREVIEW_LAYOUT="right,45%,~3,wrap-word"

# Future-proofing: Variables for external pruning scripts/cronjobs
MAX_CLIP_ITEMS=5000
MAX_CLIP_AGE_DAYS=7
EOF
fi

# Safely extract configuration to prevent syntax/command errors from breaking FZF preview
PREVIEW_LAYOUT="right,45%,~3,wrap-word"
if [[ -r "$USER_STATE_FILE" ]]; then
    _pl=$(grep '^PREVIEW_LAYOUT=' "$USER_STATE_FILE" 2>/dev/null | head -n1 | cut -d'"' -f2 | cut -d"'" -f1)
    [[ -n "$_pl" ]] && PREVIEW_LAYOUT="$_pl"
fi

# --- Persistence Integration ---
readonly STATE_FILE="${HOME}/.config/dusky/settings/clipboard_persistance"
readonly DB_ENV_FILE="${HOME}/.config/dusky/settings/cliphist_db_env"
if [[ -f "$DB_ENV_FILE" ]]; then
    # We allow sourcing this specifically because it's managed entirely by your static toggler
    source "$DB_ENV_FILE"
fi

readonly PINS_DIR="$XDG_DATA_HOME/rofi-cliphist/pins"

# --- RAM-Disk Cache ---
if [[ -d "/dev/shm" && -w "/dev/shm" ]]; then
    readonly CACHE_DIR="/dev/shm/cliphist-fzf/images"
else
    readonly CACHE_DIR="$XDG_CACHE_HOME/rofi-cliphist/images"
fi

readonly SEARCH_INDEX_MAX=10000
readonly PREVIEW_TEXT_LIMIT=50000

readonly SEP=$'\x1f'
readonly ICON_PIN="📌"
readonly ICON_IMG="📸"
readonly ICON_BIN="📦"

readonly TEXT_LOCALE="C.UTF-8"
readonly LIST_LOCALE="C"

readonly SELF="$(realpath "${BASH_SOURCE[0]}")"
readonly SCRIPT_NAME="${SELF##*/}"

if command -v b2sum &>/dev/null; then
    readonly _HASH_CMD="b2sum"
else
    readonly _HASH_CMD="md5sum"
fi

declare -a _TMPFILES=()
readonly _INVOCATION_MODE="${1:-__main__}"

#==============================================================================
# HELPERS
#==============================================================================
have() { command -v "$1" &>/dev/null; }

log_err() { printf '\e[31m[ERROR]\e[0m %s\n' "$1" >&2; }

notify() {
    local title="$1" msg="${2:-}" urgency="${3:-normal}"
    if have notify-send; then
        notify-send -u "$urgency" -a "Clipboard" "📋 $title" "$msg" 2>/dev/null
    fi
    [[ "$urgency" == "critical" ]] && log_err "$title: $msg"
}

is_uint() { [[ "${1:-}" =~ ^[0-9]+$ ]]; }
is_pin_hash() { [[ "${1:-}" =~ ^[[:xdigit:]]{16}$ ]]; }
is_kitty() { [[ -n "${KITTY_PID:-}${KITTY_WINDOW_ID:-}" || "${TERM:-}" == *kitty* ]]; }

kitty_clear() { printf '\e_Ga=d,d=A\e\\'; }

cleanup() {
    local tmp
    for tmp in "${_TMPFILES[@]}"; do
        [[ -n "${tmp:-}" && -e "$tmp" ]] && rm -f -- "$tmp" 2>/dev/null || :
    done

    if [[ "$_INVOCATION_MODE" == "__main__" ]]; then
        is_kitty && kitty_clear 2>/dev/null || :
        # Aggressively clean up any lingering F1 help state files
        [[ -d "${CACHE_DIR:-}" ]] && rm -f -- "$CACHE_DIR/.show_help_"* 2>/dev/null || :
    fi
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

dir_ready() {
    [[ -d "$1" && ! -L "$1" ]]
}

ensure_private_dir() {
    local dir="$1"
    mkdir -p -m 700 -- "$dir" 2>/dev/null || return 1
    [[ -d "$dir" && ! -L "$dir" ]] || return 1
}

setup_dirs() {
    ensure_private_dir "$PINS_DIR" || return 1
    ensure_private_dir "$CACHE_DIR" || return 1
}

make_tmpfile() {
    local dir="$1" template="${2:-.tmp.XXXXXX}" tmp
    dir_ready "$dir" || ensure_private_dir "$dir" || return 1
    tmp=$(mktemp "${dir%/}/${template}") || return 1
    _TMPFILES+=("$tmp")
    printf '%s' "$tmp"
}

untrack_tmpfile() {
    local path="$1" i
    for i in "${!_TMPFILES[@]}"; do
        [[ "${_TMPFILES[i]}" == "$path" ]] && { unset "_TMPFILES[$i]"; return 0; }
    done
    return 0
}

remove_tmpfile() {
    local path="${1:-}"
    [[ -n "$path" ]] || return 0
    rm -f -- "$path" 2>/dev/null || :
    untrack_tmpfile "$path"
}

cliphist_feed_id() { printf '%s\t\n' "$1"; }

cliphist_decode_to_file() {
    local id="$1" out="$2"
    is_uint "$id" || return 1
    cliphist_feed_id "$id" | cliphist decode > "$out" 2>/dev/null
}

decode_entry_to_tmp() {
    local id="$1" dir="$2" template="${3:-.tmp.XXXXXX}" tmp
    tmp=$(make_tmpfile "$dir" "$template") || return 1
    if cliphist_decode_to_file "$id" "$tmp"; then
        printf '%s' "$tmp"
        return 0
    fi
    remove_tmpfile "$tmp"
    return 1
}

mime_from_file() { file --mime-type -b -- "$1" 2>/dev/null; }
describe_file() { file -b -- "$1" 2>/dev/null; }
mime_is_image() { [[ "${1:-}" == image/* ]]; }

generate_hash_file() {
    local hash_line hash
    hash_line=$("$_HASH_CMD" -- "$1" 2>/dev/null) || return 1
    hash="${hash_line%% *}"
    printf '%s' "${hash:0:16}"
}

parse_item() {
    local input="$1"
    local -n _type="$2" _id="$3"
    IFS="$SEP" read -r _ _type _id _ <<< "$input"
    [[ -n "$_type" ]] || return 1
    [[ "$_type" == "empty" || "$_type" == "error" || -n "$_id" ]] || return 1
    return 0
}

proc_comm() {
    local pid="$1" comm
    [[ -r "/proc/$pid/comm" ]] || return 1
    IFS= read -r comm < "/proc/$pid/comm" || return 1
    printf '%s' "$comm"
}

proc_ppid() {
    local pid="$1" key value
    [[ -r "/proc/$pid/status" ]] || return 1
    while IFS=: read -r key value; do
        [[ "$key" == "PPid" ]] || continue
        value="${value//[[:space:]]/}"
        is_uint "$value" || return 1
        printf '%s' "$value"
        return 0
    done < "/proc/$pid/status"
    return 1
}

close_spawned_terminal() {
    [[ "${CLIPBOARD_FZF_EPHEMERAL:-0}" == "1" ]] || return 0
    local pid="$PPID" comm
    while is_uint "$pid" && (( pid > 1 )); do
        comm=$(proc_comm "$pid") || break
        case "$comm" in
            kitty|foot|alacritty)
                kill -TERM "$pid" 2>/dev/null || :
                return 0
                ;;
        esac
        pid=$(proc_ppid "$pid") || break
    done
}

#==============================================================================
# STATE FILE I/O (atomic, comment-preserving)
#==============================================================================
read_state_value() {
    local key="$1" file="$2" line
    [[ -r "$file" ]] || return 1
    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$line" =~ ^[[:space:]]*${key}[[:space:]]*=[[:space:]]*\"([^\"]*)\"[[:space:]]*$ ]] \
        || [[ "$line" =~ ^[[:space:]]*${key}[[:space:]]*=[[:space:]]*\'([^\']*)\'[[:space:]]*$ ]] \
        || [[ "$line" =~ ^[[:space:]]*${key}[[:space:]]*=[[:space:]]*([^[:space:]#]+) ]]; then
            printf '%s' "${BASH_REMATCH[1]}"
            return 0
        fi
    done < "$file"
    return 1
}

write_state_value() {
    local key="$1" value="$2" file="$3"
    local dir="${file%/*}" tmp line found=0

    [[ -d "$dir" ]] || mkdir -p -m 700 -- "$dir" 2>/dev/null || return 1
    tmp=$(mktemp "${file}.XXXXXX" 2>/dev/null) || return 1

    if [[ -f "$file" ]]; then
        while IFS= read -r line || [[ -n "$line" ]]; do
            if (( !found )) && [[ "$line" =~ ^[[:space:]]*${key}[[:space:]]*= ]]; then
                printf '%s="%s"\n' "$key" "$value"
                found=1
            else
                printf '%s\n' "$line"
            fi
        done < "$file" > "$tmp"
    fi

    (( found )) || printf '%s="%s"\n' "$key" "$value" >> "$tmp"

    if mv -f -- "$tmp" "$file" 2>/dev/null; then
        return 0
    fi
    rm -f -- "$tmp" 2>/dev/null
    return 1
}

write_preview_size() {
    local session_pid="${1:-}"
    is_uint "$session_pid" || return 0

    local p_cols="${FZF_PREVIEW_COLUMNS:-0}"
    local t_cols="${FZF_COLUMNS:-0}"
    local p_lines="${FZF_PREVIEW_LINES:-0}"
    local t_lines="${FZF_LINES:-0}"

    is_uint "$p_cols" && is_uint "$t_cols" && is_uint "$p_lines" && is_uint "$t_lines" || return 0
    (( t_cols > 0 && t_lines > 0 && p_cols > 0 )) || return 0
    [[ -d "$CACHE_DIR" ]] || return 0

    local size_file="${CACHE_DIR}/.preview_size_${session_pid}"
    local tmp
    tmp=$(mktemp "${size_file}.XXXXXX" 2>/dev/null) || return 0

    if printf '%s %s %s %s\n' "$p_cols" "$t_cols" "$p_lines" "$t_lines" > "$tmp" 2>/dev/null; then
        mv -f -- "$tmp" "$size_file" 2>/dev/null || rm -f -- "$tmp" 2>/dev/null
    else
        rm -f -- "$tmp" 2>/dev/null
    fi
    return 0
}

#==============================================================================
# TEXT PREVIEW HELPERS
#==============================================================================
safe_print_text_file() {
    local path="$1" max_chars="${2:-0}"
    LC_ALL="$TEXT_LOCALE" awk -v max_chars="$max_chars" '
    BEGIN { out = 0; truncated = 0 }
    {
        gsub(/\x1B\[[0-9;]*[a-zA-Z]/, "", $0)
        gsub(/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/, " ", $0)

        if (max_chars > 0) {
            remaining = max_chars - out
            if (remaining <= 0) { truncated = 1; exit }
            if (NR > 1) {
                if (remaining == 1) { printf "\n"; out++; truncated = 1; exit }
                printf "\n"; out++; remaining--
            }
            line_len = length($0)
            if (line_len > remaining) {
                printf "%s", substr($0, 1, remaining)
                out += remaining; truncated = 1; exit
            }
            printf "%s", $0; out += line_len
        } else {
            if (NR > 1) printf "\n"
            printf "%s", $0
        }
    }
    END { if (truncated) exit 10 }
    ' "$path"
}

render_text_preview() {
    local path="$1" max_chars="${2:-0}" status
    if have bat; then
        safe_print_text_file "$path" "$max_chars" | \
            bat --style=plain --color=always --paging=never --wrap=character \
                --language=txt --terminal-width="${FZF_PREVIEW_COLUMNS:-80}" - 2>/dev/null
        status=$?
        (( status == 0 || status == 10 )) || return "$status"
    else
        safe_print_text_file "$path" "$max_chars"
        status=$?
        (( status == 0 || status == 10 )) || return "$status"
    fi
    (( status == 10 )) && printf '\n\n\e[37m[...truncated...]\e[0m\n'
    return 0
}

#==============================================================================
# IMAGE / BINARY HANDLING
#==============================================================================
find_cached_image() {
    local id="$1" path mime
    for path in "$CACHE_DIR/${id}.img" "$CACHE_DIR/${id}.png"; do
        [[ -f "$path" && ! -L "$path" ]] || continue
        mime=$(mime_from_file "$path") || mime=""
        if mime_is_image "$mime"; then
            printf '%s' "$path"
            return 0
        fi
        rm -f -- "$path" 2>/dev/null || :
    done
    return 1
}

remove_cached_files() {
    local id="$1"
    rm -f -- "$CACHE_DIR/${id}.img" "$CACHE_DIR/${id}.png" 2>/dev/null || :
}

cache_image() {
    local id="$1" path tmp mime
    is_uint "$id" || return 1

    if path=$(find_cached_image "$id"); then
        printf '%s' "$path"
        return 0
    fi

    tmp=$(decode_entry_to_tmp "$id" "$CACHE_DIR" ".img.XXXXXX") || return 1
    mime=$(mime_from_file "$tmp") || mime=""
    if ! mime_is_image "$mime"; then
        remove_tmpfile "$tmp"
        return 1
    fi

    path="$CACHE_DIR/${id}.img"
    if mv -f -- "$tmp" "$path" 2>/dev/null; then
        untrack_tmpfile "$tmp"
        printf '%s' "$path"
        return 0
    fi

    remove_tmpfile "$tmp"
    return 1
}

copy_text_entry() {
    local id="$1"
    is_uint "$id" || return 1
    cliphist_feed_id "$id" | cliphist decode 2>/dev/null | wl-copy
    cliphist_feed_id "$id" | cliphist decode 2>/dev/null | wl-copy -p 2>/dev/null || :
}

copy_binary_entry() {
    local id="$1" tmp mime status
    tmp=$(decode_entry_to_tmp "$id" "$CACHE_DIR") || return 1
    mime=$(mime_from_file "$tmp") || mime="application/octet-stream"
    [[ -n "$mime" ]] || mime="application/octet-stream"
    wl-copy --type "$mime" < "$tmp"
    status=$?
    wl-copy -p --type "$mime" < "$tmp" 2>/dev/null || :
    remove_tmpfile "$tmp"
    return "$status"
}

copy_image_entry() {
    local id="$1" path mime
    path=$(cache_image "$id") || return 1
    mime=$(mime_from_file "$path") || return 1
    mime_is_image "$mime" || return 1
    wl-copy --type "$mime" < "$path"
    wl-copy -p --type "$mime" < "$path" 2>/dev/null || :
}

display_image() {
    local img="$1"
    local cols="${FZF_PREVIEW_COLUMNS:-40}"
    local rows="${FZF_PREVIEW_LINES:-20}"

    [[ -f "$img" ]] || { printf '\e[31mImage not found\e[0m\n'; return 1; }

    (( rows > 8 )) && (( rows -= 6 )) || rows=2
    (( cols > 4 )) && (( cols -= 4 )) || cols=2

    if is_kitty && have kitten; then
        if kitten icat --clear --transfer-mode=memory --stdin=no \
            --place="${cols}x${rows}@0x1" "$img" 2>/dev/null; then
            return 0
        fi
    fi

    if have chafa; then
        chafa -f sixel --size="${cols}x${rows}" --animate=off "$img" 2>/dev/null
        return $?
    fi

    printf '\e[33mInstall chafa or use Kitty for image preview\e[0m\n'
    return 1
}

#==============================================================================
# TIME FORMATTING
#==============================================================================
format_ts() {
    local ts="$1"
    [[ -n "$ts" && "$ts" =~ ^[0-9]+$ ]] || {
        printf '[ 🕒 Unknown Time ]'
        return 1
    }
    local now week_ago dow date_str time_str day_name
    
    printf -v now '%(%s)T' -1
    week_ago=$(( now - 604800 ))

    printf -v dow '%(%u)T' "$ts"
    case "$dow" in
        1) day_name="MON" ;; 2) day_name="TUE" ;; 3) day_name="WED" ;;
        4) day_name="THU" ;; 5) day_name="FRI" ;; 6) day_name="SAT" ;;
        7) day_name="SUN" ;; *) day_name="" ;;
    esac

    printf -v time_str '%(%-I:%M %p)T' "$ts"

    if (( ts >= week_ago )); then
        printf '[ 🕒 %s %s ]' "$day_name" "$time_str"
    else
        printf -v date_str '%(%m/%d)T' "$ts"
        printf '[ 🕒 %s %s %s ]' "$date_str" "$day_name" "$time_str"
    fi
    return 0
}

#==============================================================================
# CORE LOGIC: LIST GENERATION
#==============================================================================
cmd_list() {
    local n=0 pin hash content preview

    while IFS= read -r pin; do
        [[ -r "$pin" ]] || continue

        hash="${pin##*/}"
        hash="${hash%.pin}"
        is_pin_hash "$hash" || continue

        content=""
        LC_ALL=C IFS= read -r -d '' -n "$SEARCH_INDEX_MAX" content < "$pin" || true
        [[ -z "$content" ]] && continue

        preview="${content//$'\n'/ }"
        preview="${preview//$'\r'/}"
        preview="${preview//$'\t'/ }"
        preview="${preview//"$SEP"/ }"

        ((n++))
        ((${#preview} > SEARCH_INDEX_MAX)) && preview="${preview:0:SEARCH_INDEX_MAX}"

        printf '%d %s %s%s%s%s%s\n' \
            "$n" "$ICON_PIN" "$preview" "$SEP" "pin" "$SEP" "$hash"
    done < <(
        find "${PINS_DIR:?}" -maxdepth 1 -type f -name '*.pin' -printf '%T@\t%p\n' 2>/dev/null |
        sort -rn |
        cut -f2
    )

    cliphist list 2>/dev/null | LC_ALL="$LIST_LOCALE" awk \
        -v pin_count="$n" \
        -v icon_img="$ICON_IMG" \
        -v icon_bin="$ICON_BIN" \
        -v sep="$SEP" \
        -v max_len="$SEARCH_INDEX_MAX" \
    '
    BEGIN { FS = "\t"; n = 0 }

    /^[[:space:]]*$/ { next }

    {
        id = $1
        content = ""
        for (i = 2; i <= NF; i++) content = (i == 2) ? $i : (content "\t" $i)

        n++
        idx = n + pin_count

        if (content ~ /^\[\[ *binary data/) {
            lc = tolower(content)

            dims = ""
            if (match(content, /[0-9]+[xX][0-9]+/)) {
                dims = substr(content, RSTART, RLENGTH)
                gsub(/[xX]/, "×", dims)
            }

            fmt = ""
            if (index(lc, "png")) fmt = "PNG"
            else if (index(lc, "jpeg") || index(lc, "jpg")) fmt = "JPG"
            else if (index(lc, "gif")) fmt = "GIF"
            else if (index(lc, "webp")) fmt = "WebP"
            else if (index(lc, "bmp")) fmt = "BMP"
            else if (index(lc, "tiff")) fmt = "TIFF"
            else if (index(lc, "svg")) fmt = "SVG"
            else if (index(lc, "avif")) fmt = "AVIF"
            else if (index(lc, "heic") || index(lc, "heif")) fmt = "HEIF"
            else if (index(lc, "jxl")) fmt = "JXL"
            else if (index(lc, "ico")) fmt = "ICO"
            else if (index(lc, "pnm") || index(lc, "ppm") || index(lc, "pgm") || index(lc, "pbm")) fmt = "PNM"
            else if (index(lc, "tga")) fmt = "TGA"

            if (dims != "" || fmt != "") {
                info = ""
                if (dims != "" && fmt != "") info = dims " " fmt
                else if (dims != "") info = dims
                else info = fmt
                if (info == "") info = "Image"
                printf "%d %s %s%s%s%s%s\n", idx, icon_img, info, sep, "img", sep, id
            } else {
                info = content
                sub(/^\[\[ *binary data */, "", info)
                sub(/ *\]\]$/, "", info)
                gsub(/[[:cntrl:]]/, " ", info)
                gsub(/  +/, " ", info)
                gsub(/^ +| +$/, "", info)
                if (info == "") info = "Binary"
                if (length(info) > max_len) info = substr(info, 1, max_len)
                printf "%d %s %s%s%s%s%s\n", idx, icon_bin, info, sep, "bin", sep, id
            }
        } else {
            gsub(/[[:cntrl:]]/, " ", content)
            gsub(/  +/, " ", content)
            gsub(/^ +| +$/, "", content)
            gsub(sep, " ", content)

            if (content == "") content = "[Whitespace]"
            if (length(content) > max_len) content = substr(content, 1, max_len)
            
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
# PREVIEW STATE PERSISTENCE (FZF Transform Action)
#==============================================================================
cmd_move_preview() {
    local dir="$1"
    local current next pct

    current=$(read_state_value "PREVIEW_LAYOUT" "$USER_STATE_FILE") || current=""
    [[ -n "$current" ]] || current="right,45%,~3,wrap-word"

    pct=45
    [[ "$current" =~ ,([0-9]+)% ]] && pct="${BASH_REMATCH[1]}"

    if [[ "$dir" == "hidden" ]]; then
        if [[ "$current" == "hidden" ]]; then
            # If already hidden, unhide it defaulting back to right side
            next="right,${pct}%,~3,wrap-word"
        else
            next="hidden"
        fi
    else
        next="${dir},${pct}%,~3,wrap-word"
    fi

    write_state_value "PREVIEW_LAYOUT" "$next" "$USER_STATE_FILE" 2>/dev/null || :

    printf 'change-preview-window(%s)\n' "$next"
}

#==============================================================================
# PREVIEW LOGIC
#==============================================================================
cmd_preview() {
    local type="${1:-}" id="${2:-}" session_pid="${3:-}" pin_file img_path info tmp ts_str=""

    write_preview_size "$session_pid"

    # --- F1 HELP MENU INTERCEPT (VIM EDITION) ---
    # Intercept normal preview rendering entirely if the toggle file is present
    if [[ -n "$session_pid" && -f "${CACHE_DIR}/.show_help_${session_pid}" ]]; then
        is_kitty && kitty_clear
        printf '\e[1;36m━━━ 💡 VIM SHORTCUTS ━━━\e[0m\n\n'
        printf '  \e[33mF1\e[0m          : Toggle this help menu\n\n'
        printf '  \e[36m[ MOVEMENT & SEARCH ]\e[0m\n'
        printf '  \e[33mj / k\e[0m       : Move Cursor Down / Up\n'
        printf '  \e[33mg / G\e[0m       : Top / Bottom of list\n'
        printf '  \e[33mCtrl-D/U\e[0m    : Half page Down / Up\n'
        printf '  \e[33mh / l\e[0m       : Move cursor Left / Right (in prompt)\n'
        printf '  \e[33m/\e[0m           : Enter Search mode (Esc to exit search)\n\n'
        printf '  \e[36m[ SELECTION ]\e[0m\n'
        printf '  \e[33mv / V\e[0m       : Toggle selection under cursor\n'
        printf '  \e[33mJ / K\e[0m       : Toggle selection Down / Up\n'
        printf '  \e[33mCtrl-A\e[0m      : Select All\n\n'
        printf '  \e[36m[ PREVIEW & FILTERS ]\e[0m\n'
        printf '  \e[33mAlt-H/J/K/L\e[0m : Move Preview Panel (Left/Down/Up/Right)\n'
        printf '  \e[33mAlt-V\e[0m       : Hide / Show Preview Panel\n'
        printf '  \e[33mAlt-I\e[0m       : Filter Images Only\n'
        printf '  \e[33mAlt-P\e[0m       : Filter Pinned Only\n'
        printf '  \e[33mAlt-B\e[0m       : Filter Binaries Only\n\n'
        printf '  \e[36m[ ACTIONS ]\e[0m\n'
        printf '  \e[33mAlt-A\e[0m       : Pin selected item(s)\n'
        printf '  \e[33mAlt-D\e[0m       : Delete selected item(s)\n'
        printf '  \e[33mAlt-W\e[0m       : Wipe entire clipboard\n'
        printf '  \e[33mEnter\e[0m       : Copy selected to clipboard & exit\n'
        printf '  \e[33mq / Ctrl-C\e[0m  : Abort / Exit\n'
        return 0
    fi
    # --------------------------------------------

    is_kitty && kitty_clear

    [[ -n "$type" ]] || {
        printf '\e[37mNo selection.\e[0m\n'
        return 0
    }

    if [[ "$type" == "pin" ]]; then
        pin_file="${PINS_DIR:?}/${id}.pin"
        local ts
        ts=$(stat -c %Y "$pin_file" 2>/dev/null)
        ts_str=$(format_ts "$ts")
    fi

    case "$type" in
        empty)
            printf '\n\e[37mClipboard is empty.\nCopy something to get started!\e[0m\n'
            ;;
        error)
            printf '\n\e[31mClipboard backend unavailable.\nCheck cliphist and your session environment.\e[0m\n'
            ;;
        pin)
            is_pin_hash "$id" || { printf '\e[31mInvalid pin id.\e[0m\n'; return 1; }
            printf '\e[1;33m━━━ %s PINNED ━━━\e[0m\n' "$ICON_PIN"
            printf '\e[36m%s\e[0m\n\n' "$ts_str"
            
            if [[ -f "$pin_file" && ! -L "$pin_file" ]]; then
                render_text_preview "$pin_file" "$PREVIEW_TEXT_LIMIT" || {
                    printf '\n\e[31mFailed to render pin preview.\e[0m\n'
                    return 1
                }
            else
                printf '\e[31mPin file missing.\e[0m\n'
            fi
            ;;
        img)
            is_uint "$id" || { printf '\e[31mInvalid image id.\e[0m\n'; return 1; }
            printf '\e[1;36m━━━ %s IMAGE ━━━\e[0m\n\n' "$ICON_IMG"
            
            if img_path=$(cache_image "$id"); then
                info=$(describe_file "$img_path") || info="Unknown image data."
                printf '%s\n\n' "${info:0:120}"
                display_image "$img_path"
            else
                printf '\n\e[31mFailed to decode image.\e[0m\n'
            fi
            ;;
        bin)
            is_uint "$id" || { printf '\e[31mInvalid binary id.\e[0m\n'; return 1; }
            printf '\e[1;35m━━━ %s BINARY ━━━\e[0m\n\n' "$ICON_BIN"
            tmp=$(decode_entry_to_tmp "$id" "$CACHE_DIR") || { printf '\e[31mFailed to decode entry.\e[0m\n'; return 1; }
            
            info=$(describe_file "$tmp") || info="Unknown binary data."
            printf '%s\n\n' "${info:0:120}"
            
            if mime_is_image "$(mime_from_file "$tmp")"; then
                display_image "$tmp"
            else
                printf '\e[37mBinary preview unavailable.\e[0m\n'
            fi
            remove_tmpfile "$tmp"
            ;;
        txt)
            is_uint "$id" || { printf '\e[31mInvalid text id.\e[0m\n'; return 1; }
            printf '\e[1;32m━━━ TEXT ━━━\e[0m\n\n'
            tmp=$(decode_entry_to_tmp "$id" "$CACHE_DIR") || { printf '\e[31mFailed to decode entry.\e[0m\n'; return 1; }

            render_text_preview "$tmp" "$PREVIEW_TEXT_LIMIT" || {
                remove_tmpfile "$tmp"
                printf '\n\e[31mFailed to render text preview.\e[0m\n'
                return 1
            }
            remove_tmpfile "$tmp"
            ;;
        *)
            printf '\e[31mUnknown type: %q\e[0m\n' "$type"
            return 1
            ;;
    esac
}

#==============================================================================
# ACTIONS (Multi-Select Batch Capabilities)
#==============================================================================
cmd_copy_single() {
    local type="$1" id="$2"
    case "$type" in
        pin)
            is_pin_hash "$id" || return 1
            local pin_file="${PINS_DIR:?}/${id}.pin"
            [[ -f "$pin_file" && ! -L "$pin_file" ]] || return 1
            wl-copy < "$pin_file"
            wl-copy -p < "$pin_file" 2>/dev/null || :
            ;;
        img) copy_image_entry "$id" ;;
        bin) copy_binary_entry "$id" ;;
        txt) copy_text_entry "$id" ;;
        *) return 1 ;;
    esac
}

cmd_batch_copy() {
    local -a txt_chunks=()
    local last_non_text_type="" last_non_text_id=""
    local item type id
    
    local text_count=0
    local non_text_count=0

    for item in "$@"; do
        parse_item "$item" type id || continue
        if [[ "$type" == "txt" ]]; then
            txt_chunks+=("$(cliphist_feed_id "$id" | cliphist decode 2>/dev/null)")
            ((text_count++))
        elif [[ "$type" == "pin" ]]; then
            txt_chunks+=("$(cat "$PINS_DIR/${id}.pin" 2>/dev/null)")
            ((text_count++))
        elif [[ "$type" == "empty" || "$type" == "error" ]]; then
            continue
        else
            last_non_text_type="$type"
            last_non_text_id="$id"
            ((non_text_count++))
        fi
    done
    
    local total_items=$(( text_count + non_text_count ))

    # Data loss mitigation: Explicitly notify the user if batches are mixed or invalid
    if (( total_items > 1 )); then
        if (( text_count > 0 && non_text_count > 0 )); then
            notify "Mixed Batch Copy" "Images/Binaries ignored. Copied combined text only." "normal"
        elif (( non_text_count > 1 && text_count == 0 )); then
            notify "Multiple Images" "Cannot batch copy multiple images. Copied the last selected one." "normal"
        fi
    fi

    # Merge text selections iteratively, otherwise default to latest img/bin
    if (( text_count > 0 )); then
        local IFS=$'\n'
        local text_concat="${txt_chunks[*]}"
        printf '%s' "$text_concat" | wl-copy
        printf '%s' "$text_concat" | wl-copy -p 2>/dev/null || :
    elif [[ -n "$last_non_text_type" ]]; then
        cmd_copy_single "$last_non_text_type" "$last_non_text_id"
    fi
}

cmd_batch_pin() {
    local file="$1" input type id tmp hash pin_file
    [[ -f "$file" ]] || return 1
    while IFS= read -r input; do
        parse_item "$input" type id || continue
        if [[ "$type" == "pin" ]]; then
            rm -f -- "$PINS_DIR/${id}.pin"
        elif [[ "$type" == "txt" ]]; then
            tmp=$(decode_entry_to_tmp "$id" "$PINS_DIR" ".pin.XXXXXX") || continue
            [[ -s "$tmp" ]] || { remove_tmpfile "$tmp"; continue; }
            hash=$(generate_hash_file "$tmp") || { remove_tmpfile "$tmp"; continue; }
            pin_file="$PINS_DIR/${hash}.pin"
            mv -f -- "$tmp" "$pin_file" 2>/dev/null || remove_tmpfile "$tmp"
            untrack_tmpfile "$tmp"
        fi
    done < "$file"
}

cmd_batch_delete() {
    local file="$1" input type id
    [[ -f "$file" ]] || return 1
    while IFS= read -r input; do
        parse_item "$input" type id || continue
        if [[ "$type" == "pin" ]]; then
            rm -f -- "$PINS_DIR/${id}.pin"
        elif [[ "$type" == "txt" || "$type" == "img" || "$type" == "bin" ]]; then
            cliphist_feed_id "$id" | cliphist delete 2>/dev/null
            [[ "$type" != "txt" ]] && remove_cached_files "$id"
        fi
    done < "$file"
}

cmd_wipe() {
    local status=0
    cliphist wipe 2>/dev/null || status=$?
    rm -f -- \
        "$CACHE_DIR"/*.img \
        "$CACHE_DIR"/*.png \
        "$CACHE_DIR"/.img.?????? \
        "$CACHE_DIR"/.tmp.?????? \
        "$PINS_DIR"/.pin.?????? \
        2>/dev/null || :
    return "$status"
}

cmd_prune_cache() {
    local list_output path base id
    local -A live_ids=()

    list_output=$(cliphist list 2>/dev/null) || return 0

    while IFS=$'\t' read -r id _; do
        is_uint "$id" || continue
        live_ids["$id"]=1
    done <<< "$list_output"

    for path in "$CACHE_DIR"/*.img "$CACHE_DIR"/*.png; do
        [[ -e "$path" || -L "$path" ]] || continue
        if [[ -L "$path" ]]; then
            rm -f -- "$path" 2>/dev/null || :
            continue
        fi
        [[ -f "$path" ]] || continue

        base="${path##*/}"
        id="${base%%.*}"

        if ! is_uint "$id" || [[ -z "${live_ids[$id]+x}" ]]; then
            rm -f -- "$path" 2>/dev/null || :
        fi
    done
}

#==============================================================================
# UI & ENTRY POINT
#==============================================================================
show_menu() {
    if [[ ! -t 0 || ! -t 1 ]]; then
        local term_cmd=()
        if have kitty; then
            term_cmd=(kitty --class=cliphist-fzf --title=Clipboard -o confirm_os_window_close=0 -e env CLIPBOARD_FZF_EPHEMERAL=1 "$SELF")
        elif have foot; then
            term_cmd=(foot --app-id=cliphist-fzf --title=Clipboard --window-size-chars=95x20 env CLIPBOARD_FZF_EPHEMERAL=1 "$SELF")
        elif have alacritty; then
            term_cmd=(alacritty --class=cliphist-fzf --title=Clipboard -o window.dimensions.columns=95 -o window.dimensions.lines=20 -e env CLIPBOARD_FZF_EPHEMERAL=1 "$SELF")
        else
            notify "No terminal found." "critical"; exit 1
        fi
        exec "${term_cmd[@]}"
    fi

    local mode_label="DISK"
    if [[ -f "$STATE_FILE" ]]; then
        local p_state; read -r p_state < "$STATE_FILE" 2>/dev/null || true
        [[ "$p_state" == "false" ]] && mode_label="RAM"
    fi

    local combined_label=" 📋 Clipboard [${mode_label}] "
    local output=""

    # The session-scoped capture command. fzf substitutes nothing here; $$ is
    # expanded by the parent shell to the main script PID, so the size file is
    # keyed identically on the read side below.
    local cap="${SELF@Q} --capture-size $$"

    # Establish a fresh toggle state for the help menu based on this process ID
    local help_file="${CACHE_DIR}/.show_help_$$"
    rm -f -- "$help_file" 2>/dev/null || :

    output=$(
        cmd_list | fzf \
            --multi --ansi --reverse --no-sort --exact --cycle --scheme=history \
            --margin=0 --padding=0 --highlight-line \
            --border=rounded --border-label="$combined_label" --border-label-pos=3 \
            --info=hidden --header=" F1 Help " --header-first \
            --prompt=" 🅝  > " --pointer="▌" --delimiter="$SEP" --with-nth=1 \
            --track --id-nth=3 \
            --preview="${SELF@Q} --preview '{2}' '{3}' $$ # \$FZF_PREVIEW_COLUMNS \$FZF_PREVIEW_LINES \$FZF_COLUMNS \$FZF_LINES" \
            --preview-window="${PREVIEW_LAYOUT:-right,45%,~3,wrap-word}" \
            --bind="f1:execute-silent(if [ -f ${help_file@Q} ]; then rm -f ${help_file@Q}; else touch ${help_file@Q}; fi)+refresh-preview" \
            --bind="resize:execute-silent($cap)" \
            --bind="alt-h:transform(${SELF@Q} --move-preview left)" \
            --bind="alt-j:transform(${SELF@Q} --move-preview down)" \
            --bind="alt-k:transform(${SELF@Q} --move-preview up)" \
            --bind="alt-l:transform(${SELF@Q} --move-preview right)" \
            --bind="alt-v:transform(${SELF@Q} --move-preview hidden)" \
            --bind="alt-i:change-query($ICON_IMG )" \
            --bind="alt-p:change-query($ICON_PIN )" \
            --bind="alt-b:change-query($ICON_BIN )" \
            --bind="alt-a:execute-silent(${SELF@Q} --batch-pin {+f})+reload-sync(${SELF@Q} --list)" \
            --bind="alt-d:execute-silent(${SELF@Q} --batch-delete {+f})+reload-sync(${SELF@Q} --list)" \
            --bind="alt-w:execute-silent(${SELF@Q} --wipe)+reload-sync(${SELF@Q} --list)" \
            --bind="enter:execute-silent($cap)+accept" \
            --bind="esc:change-prompt( 🅝  > )+rebind(j,k,h,l,g,G,J,K,v,V,q,ctrl-d,ctrl-u,/)" \
            --bind="ctrl-c:execute-silent($cap)+abort" \
            --bind="j:down" \
            --bind="k:up" \
            --bind="h:backward-char" \
            --bind="l:forward-char" \
            --bind="g:first" \
            --bind="G:last" \
            --bind="J:toggle-down" \
            --bind="K:toggle-up" \
            --bind="v:toggle" \
            --bind="V:toggle" \
            --bind="ctrl-a:select-all" \
            --bind="ctrl-d:half-page-down" \
            --bind="ctrl-u:half-page-up" \
            --bind="q:execute-silent($cap)+abort" \
            --bind="/:change-prompt( 🔎 > )+unbind(j,k,h,l,g,G,J,K,v,V,q,ctrl-d,ctrl-u,/)+clear-query"
    ) || true

    # Clean up help toggle state on natural exit
    rm -f -- "$help_file" 2>/dev/null || :

    # -------------------------------------------------------------------------
    # Drag-resize persistence
    # The size file is written by `resize`, `enter`, `esc`, `ctrl-c` bindings
    # via execute-silent (synchronous in fzf), guaranteeing the file reflects
    # the dimensions at the moment of exit. We also pick up writes from the
    # preview subprocess as defense in depth. The atomic mktemp+mv in
    # write_preview_size ensures we never read a partial write.
    # -------------------------------------------------------------------------
    local size_file="${CACHE_DIR}/.preview_size_$$"
    if [[ -f "$size_file" ]]; then
        local p_cols=0 t_cols=0 p_lines=0 t_lines=0
        read -r p_cols t_cols p_lines t_lines < "$size_file" 2>/dev/null || true
        rm -f -- "$size_file" 2>/dev/null || :

        # Re-read PREVIEW_LAYOUT in case the user moved it during the session.
        local current_layout
        current_layout=$(read_state_value "PREVIEW_LAYOUT" "$USER_STATE_FILE") \
            || current_layout="$PREVIEW_LAYOUT"
        [[ -n "$current_layout" ]] || current_layout="$PREVIEW_LAYOUT"

        if [[ "$current_layout" != "hidden" ]] \
            && is_uint "$p_cols" && is_uint "$t_cols" \
            && is_uint "$p_lines" && is_uint "$t_lines" \
            && (( t_cols > 0 && t_lines > 0 )); then

            local orient="" old_pct=45
            if [[ "$current_layout" == right* || "$current_layout" == left* ]]; then
                orient="h"
            elif [[ "$current_layout" == up* || "$current_layout" == down* ]]; then
                orient="v"
            fi
            [[ "$current_layout" =~ ,([0-9]+)% ]] && old_pct="${BASH_REMATCH[1]}"

            if [[ -n "$orient" && "$current_layout" =~ ,[0-9]+% ]]; then
                # Subtract the rounded-border overhead (1 col/line each side)
                # before computing the percentage. Round to nearest integer.
                local numer denom new_pct
                if [[ "$orient" == "h" ]]; then
                    numer=$p_cols
                    denom=$(( t_cols - 2 ))
                else
                    numer=$p_lines
                    denom=$(( t_lines - 2 ))
                fi

                if (( denom > 0 && numer > 0 )); then
                    new_pct=$(( (numer * 100 + denom / 2) / denom ))
                    (( new_pct < 10 )) && new_pct=10
                    (( new_pct > 90 )) && new_pct=90

                    # Drift threshold: rounding plus border-correction error
                    # can produce a 1% delta with no actual drag. Require ≥2%
                    # change to count as a real user drag — otherwise repeated
                    # opens would slowly creep the saved value.
                    local diff=$(( new_pct > old_pct ? new_pct - old_pct : old_pct - new_pct ))
                    if (( diff >= 2 )); then
                        local new_layout="${current_layout/,${old_pct}%/,${new_pct}%}"
                        if [[ "$new_layout" != "$current_layout" ]]; then
                            write_state_value "PREVIEW_LAYOUT" "$new_layout" "$USER_STATE_FILE" 2>/dev/null || :
                        fi
                    fi
                fi
            fi
        fi
    fi

    mapfile -t lines <<< "$output"
    if ((${#lines[@]} == 0)) || [[ -z "${lines[0]:-}" ]]; then
        close_spawned_terminal
        return
    fi

    cmd_batch_copy "${lines[@]}"
    
    sleep 0.10
    close_spawned_terminal
}

main() {
    if (( BASH_VERSINFO[0] < 5 )); then
        log_err "Bash 5.0+ required (found ${BASH_VERSION})"
        exit 1
    fi

    case "${1:-}" in
        --list) cmd_list ;;
        --preview)
            [[ $# -ge 3 ]] || exit 1
            cmd_preview "$2" "$3" "${4:-}"
            ;;
        --capture-size)
            write_preview_size "${2:-}"
            ;;
        --move-preview)
            [[ $# -ge 2 ]] || exit 1
            cmd_move_preview "$2"
            ;;
        --batch-pin)
            setup_dirs >/dev/null 2>&1 || :
            cmd_batch_pin "${2:-}"
            ;;
        --batch-delete)
            setup_dirs >/dev/null 2>&1 || :
            cmd_batch_delete "${2:-}"
            ;;
        --wipe)
            setup_dirs >/dev/null 2>&1 || :
            cmd_wipe
            ;;
        --prune-cache)
            setup_dirs >/dev/null 2>&1 || :
            cmd_prune_cache
            ;;
        --help|-h)
            printf 'Usage: %s\nRun to open the clipboard menu.\n' "$SCRIPT_NAME"
            ;;
        "")
            setup_dirs || { notify "Failed to create required directories." "critical"; exit 1; }
            show_menu
            ;;
        *)
            log_err "Unknown argument: $1"
            exit 1
            ;;
    esac
}

main "$@"
