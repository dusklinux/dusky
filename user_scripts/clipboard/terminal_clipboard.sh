#!/usr/bin/env bash
#==============================================================================
# FZF CLIPBOARD MANAGER
# Arch Linux / Hyprland / UWSM clipboard utility
# Dependencies: fzf, cliphist, wl-copy, file
# Optional: chafa, bat, kitty, notify-send
#==============================================================================
# NOTE: `set -o errexit` is intentionally omitted. Several functions use
# `return 1` for normal control flow.
#==============================================================================

set -o nounset
set -o pipefail
shopt -s nullglob extglob
umask 077

#==============================================================================
# CONFIGURATION
#==============================================================================
readonly XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
readonly XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"

# --- UWSM / Persistence Integration ---
if [[ -z "${CLIPHIST_DB_PATH:-}" ]]; then
    if [[ "${DESKTOP_SESSION:-}" == *"uwsm"* || -n "${UWSM_FINALIZE_VARNAMES:-}" ]]; then
        _uwsm_env="$HOME/.config/uwsm/env"
        if [[ -f "$_uwsm_env" ]]; then
            while IFS= read -r _raw_line || [[ -n "$_raw_line" ]]; do
                _line="${_raw_line##+([[:space:]])}"
                [[ -z "$_line" || "$_line" == "#"* ]] && continue

                _line="${_line#export }"
                _line="${_line##+([[:space:]])}"

                if [[ "$_line" == "CLIPHIST_DB_PATH="* ]]; then
                    _raw_val="${_line#CLIPHIST_DB_PATH=}"

                    if [[ "$_raw_val" =~ ^\"(.*)\"$ || "$_raw_val" =~ ^\'(.*)\'$ ]]; then
                        _raw_val="${BASH_REMATCH[1]}"
                    fi

                    _raw_val="${_raw_val//\$\{XDG_RUNTIME_DIR\}/${XDG_RUNTIME_DIR:-}}"
                    _raw_val="${_raw_val//\$\{HOME\}/${HOME}}"
                    _raw_val="${_raw_val//\$XDG_RUNTIME_DIR\//${XDG_RUNTIME_DIR:-}/}"
                    _raw_val="${_raw_val//\$HOME\//${HOME}/}"

                    [[ "$_raw_val" == '$XDG_RUNTIME_DIR' ]] && _raw_val="${XDG_RUNTIME_DIR:-}"
                    [[ "$_raw_val" == '$HOME' ]] && _raw_val="${HOME}"

                    export CLIPHIST_DB_PATH="$_raw_val"
                    break
                fi
            done < "$_uwsm_env"
        fi
        unset _uwsm_env _raw_line _line _raw_val
    fi
fi

readonly PINS_DIR="$XDG_DATA_HOME/rofi-cliphist/pins"
readonly CACHE_DIR="$XDG_CACHE_HOME/rofi-cliphist/images"

readonly SEP=$'\x1f'

readonly ICON_PIN="📌"
readonly ICON_IMG="📸"
readonly ICON_BIN="📦"

readonly LIST_PREVIEW_MAX=55
readonly PIN_READ_MAX=512
readonly PREVIEW_TEXT_LIMIT=50000
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
have() {
    command -v "$1" &>/dev/null
}

log_err() {
    printf '\e[31m[ERROR]\e[0m %s\n' "$1" >&2
}

notify() {
    local msg="$1" urgency="${2:-normal}"
    if have notify-send; then
        notify-send -u "$urgency" -a "Clipboard" "📋 Clipboard" "$msg" 2>/dev/null
    fi
    [[ "$urgency" == "critical" ]] && log_err "$msg"
}

is_uint() {
    [[ "${1:-}" =~ ^[0-9]+$ ]]
}

is_pin_hash() {
    [[ "${1:-}" =~ ^[[:xdigit:]]{16}$ ]]
}

is_kitty() {
    [[ -n "${KITTY_PID:-}${KITTY_WINDOW_ID:-}" || "${TERM:-}" == *kitty* ]]
}

kitty_clear() {
    printf '\e_Ga=d,d=A\e\\'
}

cleanup() {
    local tmp
    for tmp in "${_TMPFILES[@]}"; do
        [[ -n "${tmp:-}" && -e "$tmp" ]] && rm -f -- "$tmp" 2>/dev/null || :
    done

    if [[ "$_INVOCATION_MODE" == "__main__" ]]; then
        is_kitty && kitty_clear 2>/dev/null || :
    fi
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

quote_sh() {
    local s="$1" out="'" chunk
    while [[ "$s" == *"'"* ]]; do
        chunk=${s%%"'"*}
        out+="${chunk}'\"'\"'"
        s=${s#*"'"}
    done
    out+="${s}'"
    printf '%s' "$out"
}

dir_ready() {
    [[ -d "$1" && ! -L "$1" ]]
}

ensure_private_dir() {
    local dir="$1"
    mkdir -p -- "$dir" 2>/dev/null || return 1
    [[ -d "$dir" && ! -L "$dir" ]] || return 1
    chmod 700 -- "$dir" 2>/dev/null || :
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

cliphist_feed_id() {
    printf '%s\t\n' "$1"
}

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

mime_from_file() {
    file --mime-type -b -- "$1" 2>/dev/null
}

describe_file() {
    file -b -- "$1" 2>/dev/null
}

mime_is_image() {
    [[ "${1:-}" == image/* ]]
}

generate_hash_file() {
    local hash_line hash
    hash_line=$("$_HASH_CMD" -- "$1" 2>/dev/null) || return 1
    hash="${hash_line%% *}"
    printf '%s' "${hash:0:16}"
}

parse_item() {
    local input="$1" rest
    local -n _visible="$2" _type="$3" _id="$4"

    _visible=""
    _type=""
    _id=""

    [[ "$input" == *"$SEP"*"$SEP"* ]] || return 1

    _id="${input##*"${SEP}"}"
    rest="${input%"${SEP}"*}"
    _type="${rest##*"${SEP}"}"
    _visible="${rest%"${SEP}"*}"

    [[ -n "$_type" ]] || return 1
    [[ "$_type" == "empty" || "$_type" == "error" || -n "$_id" ]] || return 1
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

check_deps() {
    local cmd missing=() msg
    for cmd in fzf cliphist wl-copy file; do
        have "$cmd" || missing+=("$cmd")
    done

    if ((${#missing[@]})); then
        printf -v msg 'Missing: %s\nInstall: sudo pacman -S fzf wl-clipboard cliphist file' "${missing[*]}"
        notify "$msg" "critical"
        exit 1
    fi

    local warn_flag="$CACHE_DIR/.warned"
    if [[ ! -f "$warn_flag" ]]; then
        local opt=()
        have chafa || opt+=("chafa")
        have bat || opt+=("bat")
        ((${#opt[@]})) && notify "Recommended: sudo pacman -S ${opt[*]}" "low"
        : > "$warn_flag" 2>/dev/null || :
    fi
}

#==============================================================================
# TEXT PREVIEW HELPERS
#==============================================================================
safe_print_text_file() {
    local path="$1" max_chars="${2:-0}"
    LC_ALL="$TEXT_LOCALE" awk -v max_chars="$max_chars" '
    BEGIN {
        esc = sprintf("%c", 27)
        out = 0
        truncated = 0
    }
    {
        gsub(esc, "", $0)
        gsub(/[[:cntrl:]]/, " ", $0)

        if (max_chars > 0) {
            remaining = max_chars - out
            if (remaining <= 0) {
                truncated = 1
                exit
            }

            if (NR > 1) {
                if (remaining == 1) {
                    printf "\n"
                    out++
                    truncated = 1
                    exit
                }
                printf "\n"
                out++
                remaining--
            }

            line_len = length($0)
            if (line_len > remaining) {
                printf "%s", substr($0, 1, remaining)
                out += remaining
                truncated = 1
                exit
            }

            printf "%s", $0
            out += line_len
        } else {
            if (NR > 1) printf "\n"
            printf "%s", $0
        }
    }
    END {
        if (truncated) exit 10
    }
    ' "$path"
}

print_text_preview() {
    local path="$1" max_chars="${2:-0}" status
    if have bat; then
        safe_print_text_file "$path" "$max_chars" | \
            bat --style=plain --color=always --paging=never --wrap=character \
                --language=txt --terminal-width="${FZF_PREVIEW_COLUMNS:-80}" - 2>/dev/null
        status=$?
        if (( status == 0 || status == 10 )); then
            return "$status"
        fi
    fi
    safe_print_text_file "$path" "$max_chars"
}

render_text_preview() {
    local path="$1" max_chars="${2:-0}" status
    print_text_preview "$path" "$max_chars"
    status=$?
    (( status == 0 || status == 10 )) || return "$status"
    (( status == 10 )) && printf '\n\n\e[90m[...truncated...]\e[0m\n'
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
}

copy_binary_entry() {
    local id="$1" tmp mime status
    tmp=$(decode_entry_to_tmp "$id" "$CACHE_DIR") || return 1
    mime=$(mime_from_file "$tmp") || mime="application/octet-stream"
    [[ -n "$mime" ]] || mime="application/octet-stream"
    wl-copy --type "$mime" < "$tmp"
    status=$?
    remove_tmpfile "$tmp"
    return "$status"
}

copy_image_entry() {
    local id="$1" path mime
    path=$(cache_image "$id") || return 1
    mime=$(mime_from_file "$path") || return 1
    mime_is_image "$mime" || return 1
    wl-copy --type "$mime" < "$path"
}

display_image() {
    local img="$1"
    local cols="${FZF_PREVIEW_COLUMNS:-40}"
    local rows="${FZF_PREVIEW_LINES:-20}"

    [[ -f "$img" ]] || { printf '\e[31mImage not found\e[0m\n'; return 1; }

    (( rows > 4 )) && (( rows -= 3 ))

    if is_kitty && have kitten; then
        if kitten icat --clear --transfer-mode=memory --stdin=no \
            --place="${cols}x${rows}@0x1" "$img" 2>/dev/null; then
            return 0
        fi
    fi

    if have chafa; then
        chafa --size="${cols}x${rows}" --animate=off "$img" 2>/dev/null
        return $?
    fi

    printf '\e[33mInstall chafa or use Kitty for image preview\e[0m\n'
    return 1
}

#==============================================================================
# CORE LOGIC: LIST GENERATION
#==============================================================================
cmd_list() {
    local n=0
    local pin hash content preview

    # Hot path: keep per-pin work minimal so rows print immediately.
    while IFS= read -r pin; do
        [[ -r "$pin" ]] || continue

        hash="${pin##*/}"
        hash="${hash%.pin}"
        is_pin_hash "$hash" || continue

        content=""
        LC_ALL=C IFS= read -r -d '' -n "$PIN_READ_MAX" content < "$pin" || true
        [[ -z "$content" ]] && continue

        preview="${content//$'\n'/ }"
        preview="${preview//$'\r'/}"
        preview="${preview//$'\t'/ }"
        preview="${preview//"$SEP"/ }"

        ((n++))
        ((${#preview} > LIST_PREVIEW_MAX)) && preview="${preview:0:LIST_PREVIEW_MAX}…"

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
        -v max_len="$LIST_PREVIEW_MAX" \
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
                if (length(info) > max_len) info = substr(info, 1, max_len) "…"
                printf "%d %s %s%s%s%s%s\n", idx, icon_bin, info, sep, "bin", sep, id
            }
        } else {
            gsub(/[[:cntrl:]]/, " ", content)
            gsub(/  +/, " ", content)
            gsub(/^ +| +$/, "", content)
            gsub(sep, " ", content)

            if (content == "") content = "[Whitespace]"
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
preview_binary_entry() {
    local id="$1" tmp mime info
    tmp=$(decode_entry_to_tmp "$id" "$CACHE_DIR") || {
        printf '\e[31mFailed to decode entry.\e[0m\n'
        return 1
    }

    mime=$(mime_from_file "$tmp") || mime=""
    info=$(describe_file "$tmp") || info="Unknown binary data."
    info="${info:0:120}"

    if mime_is_image "$mime"; then
        printf '\e[1;36m━━━ %s IMAGE ━━━\e[0m\n' "$ICON_IMG"
        printf '%s\n\n' "$info"
        display_image "$tmp"
    else
        printf '\e[1;35m━━━ %s BINARY ━━━\e[0m\n\n' "$ICON_BIN"
        printf '%s\n\n' "$info"
        printf '\e[90mBinary preview unavailable.\e[0m\n'
    fi

    remove_tmpfile "$tmp"
}

cmd_preview() {
    local input="${1:-}" visible type id pin_file img_path info tmp

    is_kitty && kitty_clear

    [[ -n "$input" ]] || {
        printf '\e[90mNo selection.\e[0m\n'
        return 0
    }

    parse_item "$input" visible type id || {
        printf '\e[31mInvalid selection.\e[0m\n'
        return 1
    }

    case "$type" in
        empty)
            printf '\n\e[90mClipboard is empty.\nCopy something to get started!\e[0m\n'
            ;;
        error)
            printf '\n\e[31mClipboard backend unavailable.\nCheck cliphist and your session environment.\e[0m\n'
            ;;
        pin)
            is_pin_hash "$id" || {
                printf '\e[31mInvalid pin id.\e[0m\n'
                return 1
            }
            printf '\e[1;33m━━━ %s PINNED ━━━\e[0m\n\n' "$ICON_PIN"
            pin_file="${PINS_DIR:?}/${id}.pin"
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
            is_uint "$id" || {
                printf '\e[31mInvalid image id.\e[0m\n'
                return 1
            }
            printf '\e[1;36m━━━ %s IMAGE ━━━\e[0m\n' "$ICON_IMG"
            if img_path=$(cache_image "$id"); then
                info=$(describe_file "$img_path") || info="Unknown image data."
                printf '%s\n\n' "${info:0:120}"
                display_image "$img_path"
            else
                printf '\n\e[31mFailed to decode image.\e[0m\n'
            fi
            ;;
        bin)
            is_uint "$id" || {
                printf '\e[31mInvalid binary id.\e[0m\n'
                return 1
            }
            preview_binary_entry "$id"
            ;;
        txt)
            is_uint "$id" || {
                printf '\e[31mInvalid text id.\e[0m\n'
                return 1
            }
            printf '\e[1;32m━━━ TEXT ━━━\e[0m\n\n'
            tmp=$(decode_entry_to_tmp "$id" "$CACHE_DIR") || {
                printf '\e[31mFailed to decode entry.\e[0m\n'
                return 1
            }

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
# ACTIONS
#==============================================================================
cmd_copy() {
    local input="$1" visible type id pin_file

    parse_item "$input" visible type id || return 1

    case "$type" in
        empty|error)
            return 0
            ;;
        pin)
            is_pin_hash "$id" || return 1
            pin_file="${PINS_DIR:?}/${id}.pin"
            [[ -f "$pin_file" && ! -L "$pin_file" ]] || return 1
            wl-copy < "$pin_file"
            ;;
        img)
            is_uint "$id" || return 1
            copy_image_entry "$id"
            ;;
        bin)
            is_uint "$id" || return 1
            copy_binary_entry "$id"
            ;;
        txt)
            is_uint "$id" || return 1
            copy_text_entry "$id"
            ;;
        *)
            return 1
            ;;
    esac
}

cmd_pin() {
    local input="$1" visible type id tmp hash pin_file

    parse_item "$input" visible type id || return 1

    case "$type" in
        pin)
            is_pin_hash "$id" || return 1
            rm -f -- "$PINS_DIR/${id}.pin"
            ;;
        txt)
            is_uint "$id" || return 1
            tmp=$(decode_entry_to_tmp "$id" "$PINS_DIR" ".pin.XXXXXX") || return 1
            [[ -s "$tmp" ]] || { remove_tmpfile "$tmp"; return 1; }

            hash=$(generate_hash_file "$tmp") || {
                remove_tmpfile "$tmp"
                return 1
            }

            pin_file="$PINS_DIR/${hash}.pin"
            if mv -f -- "$tmp" "$pin_file" 2>/dev/null; then
                untrack_tmpfile "$tmp"
                return 0
            fi

            remove_tmpfile "$tmp"
            return 1
            ;;
        *)
            return 0
            ;;
    esac
}

cmd_delete() {
    local input="$1" visible type id status

    parse_item "$input" visible type id || return 1

    case "$type" in
        empty|error)
            return 0
            ;;
        pin)
            is_pin_hash "$id" || return 1
            rm -f -- "$PINS_DIR/${id}.pin"
            ;;
        img|bin)
            is_uint "$id" || return 1
            cliphist_feed_id "$id" | cliphist delete 2>/dev/null
            status=$?
            remove_cached_files "$id"
            return "$status"
            ;;
        txt)
            is_uint "$id" || return 1
            cliphist_feed_id "$id" | cliphist delete 2>/dev/null
            ;;
        *)
            return 0
            ;;
    esac
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
            term_cmd=(
                kitty
                --class=cliphist-fzf
                --title=Clipboard
                -o confirm_os_window_close=0
                -e env CLIPBOARD_FZF_EPHEMERAL=1 "$SELF"
            )
        elif have foot; then
            term_cmd=(
                foot
                --app-id=cliphist-fzf
                --title=Clipboard
                --window-size-chars=95x20
                env CLIPBOARD_FZF_EPHEMERAL=1 "$SELF"
            )
        elif have alacritty; then
            term_cmd=(
                alacritty
                --class=cliphist-fzf
                --title=Clipboard
                -o window.dimensions.columns=95
                -o window.dimensions.lines=20
                -e env CLIPBOARD_FZF_EPHEMERAL=1 "$SELF"
            )
        else
            notify "No terminal found." "critical"
            exit 1
        fi
        exec "${term_cmd[@]}"
    fi

    local selection="" self_q copied=0
    self_q=$(quote_sh "$SELF")

    selection=$(
        cmd_list | fzf \
            --ansi --reverse --no-sort --exact --no-multi --cycle \
            --margin=0 --padding=0 \
            --border=rounded --border-label=" 📋 Clipboard " --border-label-pos=3 \
            --info=hidden --header="Alt-U pin/unpin  Alt-Y delete  Alt-T wipe" --header-first \
            --prompt="  " --pointer="▌" --delimiter="$SEP" --with-nth=1 \
            --preview="${self_q} --preview {}" --preview-window="right,45%,~1,wrap" \
            --bind="enter:accept" \
            --bind="alt-u:execute-silent(${self_q} --pin {})+reload(${self_q} --list)" \
            --bind="alt-y:execute-silent(${self_q} --delete {})+reload(${self_q} --list)" \
            --bind="alt-t:execute-silent(${self_q} --wipe)+reload(${self_q} --list)" \
            --bind="esc:abort" --bind="ctrl-c:abort"
    ) || true

    if [[ -n "$selection" ]]; then
        if cmd_copy "$selection"; then
            copied=1
        else
            notify "Failed to copy selection." "critical"
        fi
    fi

    (( copied )) && sleep 0.10
    close_spawned_terminal
}

main() {
    if (( BASH_VERSINFO[0] < 5 )); then
        log_err "Bash 5.0+ required (found ${BASH_VERSION})"
        exit 1
    fi

    case "${1:-}" in
        --list)
            cmd_list
            ;;
        --preview)
            [[ $# -ge 2 ]] || { log_err "--preview requires an item"; exit 1; }
            shift
            cmd_preview "$1"
            ;;
        --pin)
            [[ $# -ge 2 ]] || { log_err "--pin requires an item"; exit 1; }
            setup_dirs >/dev/null 2>&1 || :
            shift
            cmd_pin "$1"
            ;;
        --delete)
            [[ $# -ge 2 ]] || { log_err "--delete requires an item"; exit 1; }
            shift
            cmd_delete "$1"
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
            printf 'Usage: %s [--help]\n' "$SCRIPT_NAME"
            printf 'Run with no arguments to open the clipboard menu.\n'
            ;;
        "")
            setup_dirs || {
                notify "Failed to create required directories." "critical"
                exit 1
            }
            check_deps
            show_menu
            ;;
        *)
            log_err "Unknown argument: $1"
            exit 1
            ;;
    esac
}

main "$@"
