#!/usr/bin/env bash
#==============================================================================
# Enhanced Rofi Clipboard Manager - FINAL OPTIMIZED VERSION
#==============================================================================

set -o nounset
set -o pipefail
shopt -s nullglob

#--- CONFIGURATION ---
readonly XDG_DATA_HOME="${XDG_DATA_HOME:-${HOME}/.local/share}"
readonly XDG_CACHE_HOME="${XDG_CACHE_HOME:-${HOME}/.cache}"
readonly PINS_DIR="${XDG_DATA_HOME}/rofi-cliphist/pins"
readonly THUMB_DIR="${XDG_CACHE_HOME}/rofi-cliphist/thumbs"

# RESTORED: Icons for UI clarity (The critique removed these)
readonly PIN_ICON=" "
readonly IMG_ICON=" "

readonly MAX_PREVIEW_LENGTH=80
readonly THUMB_SIZE="256x256"

# Rofi Protocol Delimiter (Unit Separator)
readonly SEP=$'\x1f'

#--- DEPENDENCY CHECK ---
validate_dependencies() {
    local missing=() cmd
    for cmd in cliphist wl-copy; do
        command -v "${cmd}" &>/dev/null || missing+=("${cmd}")
    done

    if ((${#missing[@]} > 0)); then
        # Print to stderr so it might show in logs, but return failure
        printf 'Error: Missing dependencies: %s\n' "${missing[*]}" >&2
        return 1
    fi
    return 0
}

#--- SETUP ---
validate_dependencies || exit 1
mkdir -p "${PINS_DIR}" "${THUMB_DIR}"
chmod 700 "${PINS_DIR}" "${THUMB_DIR}"

#--- UTILS ---
generate_hash() {
    local input="$1"
    # Prefer BLAKE2 for speed, fallback to MD5
    if command -v b2sum &>/dev/null; then
        printf '%s' "${input}" | b2sum | cut -c1-16
    else
        printf '%s' "${input}" | md5sum | cut -c1-16
    fi
}

create_preview() {
    local content="$1"
    local preview

    # 1. Early Truncation: Don't process 1MB of text if we only show 80 chars
    # We grab 2x length to account for whitespace we might collapse later
    if ((${#content} > MAX_PREVIEW_LENGTH * 2)); then
        content="${content:0:$((MAX_PREVIEW_LENGTH * 2))}"
    fi

    # 2. Native Bash cleanup (No external `tr` calls)
    # Replace control chars (newlines, tabs, nulls) with spaces
    preview="${content//[$'\n\r\t\v\f\x00\x1f']/ }"

    # 3. Collapse multiple spaces into one
    while [[ "${preview}" == *"  "* ]]; do
        preview="${preview//  / }"
    done

    # 4. Trim leading/trailing whitespace
    preview="${preview#"${preview%%[![:space:]]*}"}"
    preview="${preview%"${preview##*[![:space:]]}"}"

    # 5. Final Display Truncation
    if ((${#preview} > MAX_PREVIEW_LENGTH)); then
        preview="${preview:0:MAX_PREVIEW_LENGTH}…"
    fi

    printf '%s' "${preview:-[empty]}"
}

ensure_thumbnail() {
    local id="$1"
    local thumb_path="${THUMB_DIR}/${id}.png"

    # Cache hit: Return fast
    if [[ -f "${thumb_path}" ]]; then
        printf '%s' "${thumb_path}"
        return 0
    fi

    # Check for magick before attempting generation
    if ! command -v magick &>/dev/null; then
        return 1
    fi

    # Atomic Write: Write to temp file, then move.
    # Prevents broken images if script is killed mid-write.
    local tmp_path="${thumb_path}.tmp.$$"
    
    if cliphist decode "${id}" 2>/dev/null \
        | magick - -background none -resize "${THUMB_SIZE}" "${tmp_path}" 2>/dev/null; then
        mv -f "${tmp_path}" "${thumb_path}" 2>/dev/null && {
            printf '%s' "${thumb_path}"
            return 0
        }
    fi

    # Cleanup if failed
    rm -f "${tmp_path}" 2>/dev/null
    return 1
}

#--- MAIN DISPLAY ---
display_menu() {
    # Send headers. We use \000 for null byte to be POSIXly safe in printf format strings.
    # Format: \000 command \x1f value \n
    printf '\000message\x1f<b>Enter</b>: Copy  |  <b>ALT+U</b>: Pin  |  <b>ALT+Y</b>: Delete\n'
    printf '\000use-hot-keys\x1ftrue\n'
    printf '\000keep-selection\x1ftrue\n'

    # --- 1. Pinned Items ---
    local pin_file filename content preview
    # Find pins, sort by mtime (newest first), read safely
    while IFS= read -r pin_file; do
        [[ -r "${pin_file}" ]] || continue

        filename="${pin_file##*/}"
        # Safe read using redirection
        content=$(<"${pin_file}") || continue
        preview=$(create_preview "${content}")

        # Rofi Row: Icon + Text + Invisible Info
        printf '%s %s\000info\x1fpin:%s\n' "${PIN_ICON}" "${preview}" "${filename}"
    done < <(
        find "${PINS_DIR}" -maxdepth 1 -name '*.pin' -type f \
            -printf '%T@\t%p\n' 2>/dev/null \
        | sort -t$'\t' -k1 -rn \
        | cut -f2
    )

    # --- 2. History Items ---
    local line id rest rest_lower thumb_path display_text
    while IFS= read -r line; do
        [[ -z "${line}" ]] && continue

        # Split ID (everything before first tab) and Content
        id="${line%%$'\t'*}"
        rest="${line#*$'\t'}"

        # Native Bash Regex for binary detection (Case Insensitive)
        rest_lower="${rest,,}"
        if [[ "${rest_lower}" =~ binary.*(png|jpg|jpeg|bmp|webp) ]]; then
            thumb_path=$(ensure_thumbnail "${id}") || thumb_path=""

            if [[ -n "${thumb_path}" ]]; then
                # IMAGE: Send icon path via \000icon protocol
                printf '%s: %s [Image]\000icon\x1f%s\x1finfo\x1fhist:%s\n' \
                    "${id}" "${IMG_ICON}" "${thumb_path}" "${line}"
            else
                # BINARY (No thumb): Text fallback
                printf '%s: [Binary] (No Preview)\000info\x1fhist:%s\n' \
                    "${id}" "${line}"
            fi
        else
            # TEXT ITEM
            display_text=$(create_preview "${rest}")
            printf '%s: %s\000info\x1fhist:%s\n' "${id}" "${display_text}" "${line}"
        fi
    done < <(cliphist list 2>/dev/null)
}

#--- ACTION HANDLERS ---
handle_selection() {
    local selection="${1:-}"
    local action="${ROFI_RETV:-0}"
    local info="${ROFI_INFO:-}"

    # If nothing selected, just show menu
    if [[ -z "${selection}" ]]; then
        display_menu
        return 0
    fi

    local type="${info%%:*}"
    local data="${info#*:}"

    # Handle Pins
    if [[ "${type}" == "pin" ]]; then
        case "${action}" in
            1)  # Enter: Copy using input redirection (avoids cat)
                [[ -r "${PINS_DIR}/${data}" ]] && wl-copy < "${PINS_DIR}/${data}"
                ;;
            10|11) # Alt+U/Y: Delete pin
                rm -f "${PINS_DIR}/${data}"
                display_menu
                ;;
            *) display_menu ;;
        esac
    
    # Handle History
    else
        local id="${data%%$'\t'*}"
        case "${action}" in
            1)  # Enter: Decode and copy
                cliphist decode "${id}" 2>/dev/null | wl-copy
                ;;
            10) # Alt+U: Pin (Text only)
                local txt hash
                txt=$(cliphist decode "${id}" 2>/dev/null) || txt=""
                if [[ -n "${txt}" ]]; then
                    hash=$(generate_hash "${txt}")
                    printf '%s' "${txt}" > "${PINS_DIR}/${hash}.pin"
                fi
                display_menu
                ;;
            11) # Alt+Y: Delete from history + cache
                cliphist delete "${id}" 2>/dev/null
                rm -f "${THUMB_DIR}/${id}.png"
                display_menu
                ;;
            *) display_menu ;;
        esac
    fi
}

#--- ENTRY POINT ---
# Check for arguments. Rofi passes the selection as $1.
if (($# == 0)); then
    display_menu
else
    handle_selection "${1:-}"
fi
