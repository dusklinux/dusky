#!/usr/bin/env bash
#==============================================================================
# Enhanced Rofi Clipboard Manager with cliphist integration
#
# FEATURES:
#   • Works with existing cliphist setup
#   • Persistent pinned items (survive reboots)
#   • Most recently pinned items appear at the top
#   • Clean display: only pin icon shown, no hash filenames
#   • Minimal spacing between index and content
#   • Menu stays open after pin/unpin/delete operations
#
# USAGE:
#   rofi -kb-custom-1 "ALT+U" -kb-custom-2 "ALT+Y" \
#        -modi "clipboard:~/user_scripts/rofi/rofi_cliphist.sh" \
#        -show clipboard
#
#   Or create an alias/wrapper script for convenience.
#
# KEYBINDINGS:
#   Enter  → Copy selected item to clipboard (closes menu)
#   ALT+U  → Pin item (or unpin if already pinned) (stays open)
#   ALT+Y  → Delete item from history (stays open)
#==============================================================================

# Defensive shell options (errexit removed intentionally - we handle errors
# explicitly and need the script to continue for menu refresh)
set -o nounset
set -o pipefail
shopt -s nullglob

#==============================================================================
# CONFIGURATION
#==============================================================================

readonly XDG_DATA_HOME="${XDG_DATA_HOME:-${HOME}/.local/share}"
readonly PINS_DIR="${XDG_DATA_HOME}/rofi-cliphist/pins"
readonly PIN_ICON=" |"
readonly MAX_PREVIEW_LENGTH=80

# Determine hash command once at startup (prefer fastest secure option)
if command -v b2sum &>/dev/null; then
    readonly HASH_CMD="b2sum"
elif command -v sha256sum &>/dev/null; then
    readonly HASH_CMD="sha256sum"
else
    readonly HASH_CMD="md5sum"
fi

#==============================================================================
# UTILITY FUNCTIONS
#==============================================================================

log_error() {
    printf '[ERROR] %s\n' "$*" >&2
}

# Generate a short hash for content identification
# Uses pre-determined hash command for efficiency
generate_hash() {
    printf '%s' "$1" | "$HASH_CMD" | cut -c1-16
}

# Create a single-line preview suitable for rofi display
# Strips control characters, collapses whitespace, truncates
create_preview() {
    local content="$1"
    local preview
    
    # Use tr for efficient whitespace normalization and control char removal
    # \x00 and \x1f are rofi protocol control characters
    preview=$(printf '%s' "$content" | tr '\n\r\t\v\f\x00\x1f' ' ' | tr -s ' ')
    
    # Trim leading whitespace
    preview="${preview#"${preview%%[![:space:]]*}"}"
    # Trim trailing whitespace  
    preview="${preview%"${preview##*[![:space:]]}"}"
    
    # Truncate with ellipsis if too long
    if ((${#preview} > MAX_PREVIEW_LENGTH)); then
        preview="${preview:0:MAX_PREVIEW_LENGTH}…"
    fi
    
    # Handle empty content
    printf '%s' "${preview:-[empty]}"
}

#==============================================================================
# INITIALIZATION
#==============================================================================

init() {
    # Create pins directory with secure permissions
    if [[ ! -d "${PINS_DIR}" ]]; then
        mkdir -p "${PINS_DIR}"
        chmod 700 "${PINS_DIR}"
    fi
    
    # Verify required commands exist
    local cmd
    for cmd in cliphist wl-copy find; do
        if ! command -v "${cmd}" &>/dev/null; then
            log_error "Missing required command: ${cmd}"
            exit 1
        fi
    done
}

#==============================================================================
# PIN MANAGEMENT
#==============================================================================

# List pins sorted by modification time (newest first)
# Uses rofi's info field to store filename metadata separately from display
list_pins() {
    local pin_file filename content preview
    
    # GNU find with -printf for mtime, sort numerically descending
    while IFS= read -r pin_file; do
        [[ -f "${pin_file}" ]] || continue
        
        filename="${pin_file##*/}"
        content=$(<"${pin_file}") 2>/dev/null || continue
        preview=$(create_preview "${content}")
        
        # Format: "📌 preview text" with filename stored in rofi's info field
        # No visible filename or excessive spacing
        printf '%s %s\x00info\x1fpin:%s\n' \
            "${PIN_ICON}" \
            "${preview}" \
            "${filename}"
    done < <(
        find "${PINS_DIR}" -maxdepth 1 -name '*.pin' -type f \
            -printf '%T@\t%p\n' 2>/dev/null \
        | sort -t$'\t' -k1 -rn \
        | cut -f2
    )
}

# Create or update a pin
# If already pinned, updates mtime to make it "newest"
create_pin() {
    local content="$1"
    local hash_id pin_path
    
    hash_id=$(generate_hash "${content}")
    pin_path="${PINS_DIR}/${hash_id}.pin"
    
    if [[ -f "${pin_path}" ]]; then
        # Already pinned - touch to update mtime (moves to top)
        touch "${pin_path}"
        return 0
    fi
    
    # Atomic-ish write with secure permissions
    printf '%s' "${content}" > "${pin_path}"
    chmod 600 "${pin_path}"
}

# Safely delete a pin by filename
delete_pin() {
    local filename="$1"
    
    # Security: prevent path traversal attacks
    if [[ "${filename}" == *'/'* || "${filename}" == '..'* ]]; then
        log_error "Invalid pin filename: ${filename}"
        return 1
    fi
    
    local pin_path="${PINS_DIR}/${filename}"
    [[ -f "${pin_path}" ]] && rm -f "${pin_path}"
    return 0
}

# Retrieve pin content by filename
get_pin_content() {
    local filename="$1"
    
    # Security check
    if [[ "${filename}" == *'/'* || "${filename}" == '..'* ]]; then
        log_error "Invalid pin filename: ${filename}"
        return 1
    fi
    
    local pin_path="${PINS_DIR}/${filename}"
    if [[ -f "${pin_path}" ]]; then
        cat "${pin_path}"
    else
        log_error "Pin not found: ${filename}"
        return 1
    fi
}

#==============================================================================
# ROFI INTERFACE
#==============================================================================

# Display the main menu with pins first, then clipboard history
display_menu() {
    # Rofi message bar with keybinding hints
    printf '\x00message\x1f<b>Enter</b>: Copy  │  <b>ALT+U</b>: Pin/Unpin  │  <b>ALT+Y</b>: Delete\n'
    
    # Enable custom hotkeys
    printf '\x00use-hot-keys\x1ftrue\n'
    
    # Attempt to keep selection position after refresh
    printf '\x00keep-selection\x1ftrue\n'
    
    # List pinned items first (newest at top)
    list_pins
    
    # List clipboard history from cliphist with cleaned formatting
    local line display_line
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        
        # Replace tab with ": " for minimal, clean spacing
        # Original format: "42\tSome clipboard text..."
        # Display format:  "42: Some clipboard text..."
        display_line="${line/$'\t'/: }"
        
        # Sanitize display for rofi protocol
        display_line="${display_line//$'\x00'/}"
        display_line="${display_line//$'\x1f'/}"
        
        # Store original line in info for cliphist decode/delete operations
        printf '%s\x00info\x1fhist:%s\n' "${display_line}" "${line}"
    done < <(cliphist list 2>/dev/null)
}

# Route selection to appropriate handler based on item type
handle_selection() {
    local selection="${1:-}"
    local action="${ROFI_RETV:-0}"
    local info="${ROFI_INFO:-}"
    
    # No selection - redisplay menu
    if [[ -z "${selection}" ]]; then
        display_menu
        return 0
    fi
    
    # Parse item type and data from info field
    local item_type="${info%%:*}"
    local item_data="${info#*:}"
    
    case "${item_type}" in
        pin)
            handle_pinned_item "${item_data}" "${action}"
            ;;
        hist)
            handle_history_item "${item_data}" "${action}"
            ;;
        *)
            # Fallback for unexpected format - treat as history item
            log_error "Unknown item type, attempting history fallback"
            handle_history_item "${selection}" "${action}"
            ;;
    esac
}

# Handle actions on pinned items
handle_pinned_item() {
    local filename="$1"
    local action="$2"
    
    case "${action}" in
        1)  # Enter - copy to clipboard and exit
            get_pin_content "${filename}" | wl-copy
            # No output = rofi closes
            ;;
        10) # kb-custom-1 (Alt+u) - unpin item, refresh menu
            delete_pin "${filename}"
            display_menu
            ;;
        11) # kb-custom-2 (Alt+y) - delete item, refresh menu
            delete_pin "${filename}"
            display_menu
            ;;
        *)  # Unknown action - refresh menu
            display_menu
            ;;
    esac
}

# Handle actions on clipboard history items
handle_history_item() {
    local original_line="$1"
    local action="$2"
    local content
    
    case "${action}" in
        1)  # Enter - copy to clipboard and exit
            printf '%s' "${original_line}" | cliphist decode | wl-copy
            # No output = rofi closes
            ;;
        10) # kb-custom-1 (Alt+u) - pin this item, refresh menu
            content=$(printf '%s' "${original_line}" | cliphist decode 2>/dev/null) || content=""
            if [[ -n "${content}" ]]; then
                create_pin "${content}"
            fi
            display_menu
            ;;
        11) # kb-custom-2 (Alt+y) - delete from history, refresh menu
            printf '%s' "${original_line}" | cliphist delete 2>/dev/null || true
            display_menu
            ;;
        *)  # Unknown action - refresh menu
            display_menu
            ;;
    esac
}

#==============================================================================
# MAIN ENTRY POINT
#==============================================================================

main() {
    init
    
    if (($# == 0)); then
        # Initial invocation - display the menu
        display_menu
    else
        # Called with selection - handle it
        handle_selection "$*"
    fi
}

main "$@"
