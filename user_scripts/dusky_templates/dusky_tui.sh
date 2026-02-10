#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Dusky TUI Engine - Hybrid Master v3.6.1 (Architect Edition)
# -----------------------------------------------------------------------------
# Target: Arch Linux / Hyprland / UWSM / Wayland
#
# v3.6.1 CHANGELOG:
#   - FIX: Corrected Bash nameref binding in mouse handler (logic error).
#   - FIX: Adjusted sed regex to correctly handle '#' starting values (hex colors).
#   - SAFETY: Added guard clause for invalid Bash identifiers in menu IDs.
#
# v3.6.0 CHANGELOG:
#   - CRITICAL: Fixed "Trap" bug where q/Ctrl-C were ignored in submenus.
#   - CRITICAL: Fixed Config Parser corrupting '#' based color values.
#   - UX: Restored Mouse support in Submenus (was broken in v3.5.3).
#   - UX: Restored 'Enter' key toggling items (if not a menu).
#   - VISUAL: Fixed negative padding glitches on long menu titles.
# -----------------------------------------------------------------------------

set -euo pipefail

# =============================================================================
# ▼ USER CONFIGURATION (EDIT THIS SECTION) ▼
# =============================================================================

# POINT THIS TO YOUR REAL CONFIG FILE
readonly CONFIG_FILE="${HOME}/.config/hypr/change_me.conf"
readonly APP_TITLE="Input Config Editor"
readonly APP_VERSION="v3.6.1 (Patched)"

# Dimensions & Layout
declare -ri MAX_DISPLAY_ROWS=14
declare -ri BOX_INNER_WIDTH=76
declare -ri ADJUST_THRESHOLD=38
declare -ri ITEM_PADDING=32

declare -ri HEADER_ROWS=4
declare -ri TAB_ROW=3
declare -ri ITEM_START_ROW=$(( HEADER_ROWS + 1 ))

declare -ra TABS=("General" "Input" "Display" "Misc")

# Item Registration
register_items() {
    # Tab 0: General
    register 0 "Enable Logs"    'logs_enabled|bool|general|||'          "true"
    register 0 "Timeout (ms)"   'timeout|int|general|0|1000|50'        "100"
    
    # Tab 1: Input
    register 1 "Sensitivity"    'sensitivity|float|input|-1.0|1.0|0.1' "0.0"
    register 1 "Accel Profile"  'accel_profile|cycle|input|flat,adaptive,custom||' "adaptive"
    
    # Tab 2: Display
    register 2 "Border Size"    'border_size|int||0|10|1'              "2"
    register 2 "Blur Enabled"   'blur|bool|decoration|||'              "true"
    
    # Tab 3: Misc
    # 'menu' type item. The 'key' (advanced_settings) is used as the Menu ID.
    register 3 "Advanced Settings" 'advanced_settings|menu||||'        ""
    
    # Submenu Items (registered to parent ID "advanced_settings")
    register_child "advanced_settings" "Touchpad Enable"  'enabled|bool|touchpad|||' "true"
    register_child "advanced_settings" "Scroll Factor"    'scroll_factor|float|touchpad|0.1|5.0|0.1' "1.0"
    register_child "advanced_settings" "Tap to Click"     'tap-to-click|bool|touchpad|||' "true"
    
    register 3 "Shadow Color"   'col.shadow|cycle|general|0xee1a1a1a,0xff000000||' "0xee1a1a1a"
}

# Post-Write Hook
post_write_action() {
    : # Reload command here
}

# =============================================================================
# ▲ END OF USER CONFIGURATION ▲
# =============================================================================

# --- Pre-computed Constants ---
declare _h_line_buf
printf -v _h_line_buf '%*s' "$BOX_INNER_WIDTH" ''
readonly H_LINE="${_h_line_buf// /─}"
unset _h_line_buf

# --- ANSI Constants ---
readonly C_RESET=$'\033[0m'
readonly C_CYAN=$'\033[1;36m'
readonly C_GREEN=$'\033[1;32m'
readonly C_MAGENTA=$'\033[1;35m'
readonly C_RED=$'\033[1;31m'
readonly C_YELLOW=$'\033[1;33m'
readonly C_WHITE=$'\033[1;37m'
readonly C_GREY=$'\033[1;30m'
readonly C_INVERSE=$'\033[7m'
readonly CLR_EOL=$'\033[K'
readonly CLR_EOS=$'\033[J'
readonly CLR_SCREEN=$'\033[2J'
readonly CURSOR_HOME=$'\033[H'
readonly CURSOR_HIDE=$'\033[?25l'
readonly CURSOR_SHOW=$'\033[?25h'
readonly MOUSE_ON=$'\033[?1000h\033[?1002h\033[?1006h'
readonly MOUSE_OFF=$'\033[?1000l\033[?1002l\033[?1006l'

# ANSI stripping regex pattern
readonly _ESC=$'\033'

readonly ESC_READ_TIMEOUT=0.05
readonly UNSET_MARKER='«unset»'

# --- State Management ---
declare -i SELECTED_ROW=0
declare -i CURRENT_TAB=0
declare -i SCROLL_OFFSET=0
declare -ri TAB_COUNT=${#TABS[@]}
declare -a TAB_ZONES=()
declare ORIGINAL_STTY=""

# View State
declare -i CURRENT_VIEW=0      # 0=Main List, 1=Detail/Sub-Page
declare CURRENT_MENU_ID=""     # ID of the currently open menu
declare -i PARENT_ROW=0        # Saved row to return to
declare -i PARENT_SCROLL=0     # Saved scroll to return to

# --- Data Structures ---
declare -A ITEM_MAP=()
declare -A VALUE_CACHE=()
declare -A CONFIG_CACHE=()
declare -A DEFAULTS=()

# Initialize Tab arrays
for (( _ti = 0; _ti < TAB_COUNT; _ti++ )); do
    declare -ga "TAB_ITEMS_${_ti}=()"
done
unset _ti

# --- System Helpers ---

log_err() {
    printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$1" >&2
}

cleanup() {
    printf '%s%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" "$C_RESET" 2>/dev/null || :
    if [[ -n "${ORIGINAL_STTY:-}" ]]; then
        stty "$ORIGINAL_STTY" 2>/dev/null || :
    fi
    printf '\n' 2>/dev/null || :
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# --- Logic Helpers ---

escape_sed_replacement() {
    local _esc_input=$1
    local -n _esc_out_ref=$2
    _esc_input=${_esc_input//\\/\\\\}
    _esc_input=${_esc_input//|/\\|}
    _esc_input=${_esc_input//&/\\&}
    _esc_input=${_esc_input//$'\n'/\\n}
    _esc_out_ref=$_esc_input
}

escape_sed_pattern() {
    local _esc_input=$1
    local -n _esc_out_ref=$2
    _esc_input=${_esc_input//\\/\\\\}
    _esc_input=${_esc_input//|/\\|}
    _esc_input=${_esc_input//./\\.}
    _esc_input=${_esc_input//\*/\\*}
    _esc_input=${_esc_input//\[/\\[}
    _esc_input=${_esc_input//\]/\\]}
    _esc_input=${_esc_input//^/\\^}
    _esc_input=${_esc_input//\$/\\\$}
    _esc_out_ref=$_esc_input
}

# --- Core Logic Engine ---

register() {
    local -i tab_idx=$1
    local label=$2 config=$3 default_val=${4:-}
    local key type block min max step
    IFS='|' read -r key type block min max step <<< "$config"

    case "$type" in
        bool|int|float|cycle|menu) ;;
        *) log_err "Invalid type for '${label}': ${type}"; exit 1 ;;
    esac

    ITEM_MAP["${tab_idx}::${label}"]=$config
    [[ -n "$default_val" ]] && DEFAULTS["${tab_idx}::${label}"]=$default_val
    local -n _reg_tab_ref="TAB_ITEMS_${tab_idx}"
    _reg_tab_ref+=("$label")
}

register_child() {
    local parent_id=$1
    local label=$2 config=$3 default_val=${4:-}

    # SAFETY: Ensure parent_id is a valid bash identifier to prevent crashing 'declare'
    if [[ ! "$parent_id" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
        log_err "Register Error: Menu ID '${parent_id}' contains invalid characters. Use alphanumeric/underscores only."
        exit 1
    fi
    
    # Initialize submenu array if it doesn't exist
    if ! declare -p "SUBMENU_ITEMS_${parent_id}" &>/dev/null; then
        declare -ga "SUBMENU_ITEMS_${parent_id}=()"
    fi

    # Map item using Parent ID as context
    ITEM_MAP["${parent_id}::${label}"]=$config
    [[ -n "$default_val" ]] && DEFAULTS["${parent_id}::${label}"]=$default_val
    
    local -n _child_ref="SUBMENU_ITEMS_${parent_id}"
    _child_ref+=("$label")
}

strip_ansi() {
    local s="$1"
    local result=""
    while [[ -n "$s" ]]; do
        if [[ "$s" == "${_ESC}"* ]]; then
            s="${s:1}"
            if [[ "$s" == "["* ]]; then
                s="${s:1}"
                while [[ -n "$s" && ! "$s" =~ ^[a-zA-Z] ]]; do s="${s:1}"; done
                [[ -n "$s" ]] && s="${s:1}"
            else
                [[ -n "$s" ]] && s="${s:1}"
            fi
        else
            result+="${s:0:1}"
            s="${s:1}"
        fi
    done
    REPLY="$result"
}

populate_config_cache() {
    CONFIG_CACHE=()
    local key_part value_part key_name

    while IFS='=' read -r key_part value_part || [[ -n ${key_part:-} ]]; do
        [[ -z ${key_part:-} ]] && continue
        CONFIG_CACHE["$key_part"]=$value_part
        key_name=${key_part%%|*}
        if [[ -z ${CONFIG_CACHE["${key_name}|"]:-} ]]; then
            CONFIG_CACHE["${key_name}|"]=$value_part
        fi
    done < <(LC_NUMERIC=C awk '
        BEGIN { depth = 0 }
        /^[[:space:]]*#/ { next }
        {
            line = $0
            
            tmpline = line
            # Remove blocks { ... } from processing
            while (match(tmpline, /[a-zA-Z0-9_.:-]+[[:space:]]*\{/)) {
                block_str = substr(tmpline, RSTART, RLENGTH)
                sub(/[[:space:]]*\{/, "", block_str)
                depth++
                block_stack[depth] = block_str
                tmpline = substr(tmpline, RSTART + RLENGTH)
            }
            if (line ~ /=/) {
                eq_pos = index(line, "=")
                if (eq_pos > 0) {
                    key = substr(line, 1, eq_pos - 1)
                    val = substr(line, eq_pos + 1)
                    gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
                    
                    # FIX: Trim whitespace BEFORE comment stripping.
                    # This ensures values like " #ff0000" (leading space) are treated as "#ff0000"
                    # instead of being mistaken for a comment " #...".
                    gsub(/^[[:space:]]+|[[:space:]]+$/, "", val)

                    # Safe comment stripping:
                    # Now that we trimmed the leading space, we only strip if there is
                    # a space followed by a # INSIDE the value (e.g. "true # comment")
                    sub(/[[:space:]]+#.*$/, "", val)
                    
                    # Final trim (just in case stripping left trailing spaces)
                    gsub(/^[[:space:]]+|[[:space:]]+$/, "", val)
                    
                    if (key != "") {
                        current_block = (depth > 0) ? block_stack[depth] : ""
                        print key "|" current_block "=" val
                    }
                }
            }
            n = gsub(/\}/, "}", line)
            while (n > 0 && depth > 0) { depth--; n-- }
        }
    ' "$CONFIG_FILE")
}

find_key_line_in_block() {
    local block_name=$1 key_name=$2 file=$3
    LC_NUMERIC=C awk -v target_block="$block_name" -v target_key="$key_name" '
    BEGIN { depth = 0; in_target = 0; target_depth = 0; found = 0 }
    {
        line = $0; 
        # Only strip full-line comments or trailing comments safely
        clean = $0
        sub(/^[[:space:]]*#.*/, "", clean)
        sub(/[[:space:]]+#.*$/, "", clean)

        tmpline = clean
        while (match(tmpline, /[a-zA-Z0-9_.:-]+[[:space:]]*\{/)) {
            block_str = substr(tmpline, RSTART, RLENGTH)
            sub(/[[:space:]]*\{/, "", block_str)
            depth++
            block_stack[depth] = block_str
            if (block_str == target_block && !in_target) { in_target = 1; target_depth = depth }
            tmpline = substr(tmpline, RSTART + RLENGTH)
        }
        if (in_target && clean ~ /=/) {
            eq_pos = index(clean, "=")
            if (eq_pos > 0) {
                k = substr(clean, 1, eq_pos - 1)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", k)
                if (k == target_key) { print NR; found = 1 }
            }
        }
        n = gsub(/\}/, "}", clean)
        while (n > 0 && depth > 0) {
            if (in_target && depth == target_depth) { in_target = 0; target_depth = 0 }
            depth--; n--
        }
    }
    ' "$file"
}

write_value_to_file() {
    local key=$1 new_val=$2 block=${3:-}
    local current_val=${CONFIG_CACHE["$key|$block"]:-}
    [[ "$current_val" == "$new_val" ]] && return 0

    local safe_val safe_sed_key
    escape_sed_replacement "$new_val" safe_val
    escape_sed_pattern "$key" safe_sed_key

    if [[ -n "$block" ]]; then
        local target_output
        target_output=$(find_key_line_in_block "$block" "$key" "$CONFIG_FILE")
        if [[ -z "$target_output" ]]; then return 1; fi
        local target_line
        while IFS= read -r target_line; do
            [[ ! "$target_line" =~ ^[0-9]+$ ]] && continue
            (( target_line == 0 )) && continue
            sed --follow-symlinks -i "${target_line}s|^\([[:space:]]*${safe_sed_key}[[:space:]]*=[[:space:]]*\).*|\1${safe_val}|" "$CONFIG_FILE"
        done <<< "$target_output"
    else
        if [[ -z "${CONFIG_CACHE["$key|"]:-}" ]]; then
            return 1
        fi
        sed --follow-symlinks -i "s|^\([[:space:]]*${safe_sed_key}[[:space:]]*=[[:space:]]*\).*|\1${safe_val}|" "$CONFIG_FILE"
    fi

    CONFIG_CACHE["$key|$block"]=$new_val
    if [[ -z "$block" ]]; then CONFIG_CACHE["$key|"]=$new_val; fi
    return 0
}

# --- Context Helpers ---

get_active_context() {
    # Returns the active item list array name in REPLY_REF
    # Returns the active context ID (for ITEM_MAP) in REPLY_CTX
    if (( CURRENT_VIEW == 0 )); then
        REPLY_CTX="${CURRENT_TAB}"
        REPLY_REF="TAB_ITEMS_${CURRENT_TAB}"
    else
        REPLY_CTX="${CURRENT_MENU_ID}"
        REPLY_REF="SUBMENU_ITEMS_${CURRENT_MENU_ID}"
    fi
}

load_active_values() {
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _lav_items_ref="$REPLY_REF"
    local item key type block val

    for item in "${_lav_items_ref[@]}"; do
        IFS='|' read -r key type block _ _ _ <<< "${ITEM_MAP["${REPLY_CTX}::${item}"]}"
        val=${CONFIG_CACHE["$key|$block"]:-}
        if [[ -z "$val" && -z "$block" ]]; then
            val=${CONFIG_CACHE["$key|"]:-}
        fi
        if [[ -z "$val" ]]; then
            VALUE_CACHE["${REPLY_CTX}::${item}"]=$UNSET_MARKER
        else
            VALUE_CACHE["${REPLY_CTX}::${item}"]=$val
        fi
    done
}

modify_value() {
    local label=$1
    local -i direction=$2
    local REPLY_REF REPLY_CTX
    get_active_context

    local key type block min max step current new_val
    IFS='|' read -r key type block min max step <<< "${ITEM_MAP["${REPLY_CTX}::${label}"]}"
    current=${VALUE_CACHE["${REPLY_CTX}::${label}"]:-}

    if [[ "$current" == "$UNSET_MARKER" || -z "$current" ]]; then
        current=${DEFAULTS["${REPLY_CTX}::${label}"]:-}
        [[ -z "$current" ]] && current=${min:-0}
    fi

    case "$type" in
        int)
            if [[ ! "$current" =~ ^-?[0-9]+$ ]]; then current=${min:-0}; fi

            # --- FIX: Sanitize Octal (strip leading zeros to force Base-10) ---
            local clean_val=${current#-}    # Remove negative sign if present
            clean_val=$(( 10#$clean_val ))  # Force Base-10 on the number (fixes 008/009 crash)
            
            # Re-apply negative if original had it
            if [[ "$current" == -* ]]; then
                clean_val=$(( clean_val * -1 )) 
            fi

            local -i int_step=${step:-1} int_val=$clean_val
            int_val=$(( int_val + direction * int_step ))
            
            if [[ -n "$min" ]] && (( int_val < min )); then int_val=$min; fi
            if [[ -n "$max" ]] && (( int_val > max )); then int_val=$max; fi
            new_val=$int_val
            ;;
        float)
            if [[ ! "$current" =~ ^-?[0-9]*\.?[0-9]+$ ]]; then current=${min:-0.0}; fi
            new_val=$(LC_NUMERIC=C awk -v c="$current" -v dir="$direction" -v s="${step:-0.1}" \
                          -v mn="$min" -v mx="$max" 'BEGIN {
                val = c + (dir * s)
                if (mn != "" && val < mn) val = mn
                if (mx != "" && val > mx) val = mx
                str = sprintf("%.6f", val);
                sub(/0+$/, "", str);
                sub(/\.$/, "", str);
                print str;
            }')
            ;;
        bool)
            if [[ "$current" == "true" ]]; then new_val="false"; else new_val="true"; fi
            ;;
        cycle)
            local -a opts
            IFS=',' read -r -a opts <<< "$min"
            local -i count=${#opts[@]} idx=0 i
            (( count == 0 )) && return 0
            for (( i = 0; i < count; i++ )); do
                if [[ "${opts[i]}" == "$current" ]]; then idx=$i; break; fi
            done
            idx=$(( (idx + direction + count) % count ))
            new_val=${opts[idx]}
            ;;
        menu) return 0 ;;
        *) return 0 ;;
    esac

    if write_value_to_file "$key" "$new_val" "$block"; then
        VALUE_CACHE["${REPLY_CTX}::${label}"]=$new_val
        post_write_action
    fi
}

set_absolute_value() {
    local label=$1 new_val=$2
    local REPLY_REF REPLY_CTX
    get_active_context
    local key type block
    IFS='|' read -r key type block _ _ _ <<< "${ITEM_MAP["${REPLY_CTX}::${label}"]}"
    if write_value_to_file "$key" "$new_val" "$block"; then
        VALUE_CACHE["${REPLY_CTX}::${label}"]=$new_val
        return 0
    fi
    return 1
}

reset_defaults() {
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _rd_items_ref="$REPLY_REF"
    local item def_val
    local -i any_written=0
    for item in "${_rd_items_ref[@]}"; do
        def_val=${DEFAULTS["${REPLY_CTX}::${item}"]:-}
        if [[ -n "$def_val" ]]; then
            if set_absolute_value "$item" "$def_val"; then
                any_written=1
            fi
        fi
    done
    (( any_written )) && post_write_action
    return 0
}

# --- UI Rendering ---

draw_main_view() {
    local buf="" pad_buf="" padded_item="" item val display type config
    local -i i current_col=3 zone_start len count pad_needed
    local -i visible_len left_pad right_pad
    local -i visible_start visible_end

    buf+="${CURSOR_HOME}"
    buf+="${C_MAGENTA}┌${H_LINE}┐${C_RESET}"$'\n'

    strip_ansi "$APP_TITLE"; local t_len=${#REPLY}
    strip_ansi "$APP_VERSION"; local v_len=${#REPLY}
    visible_len=$(( t_len + v_len + 1 ))
    left_pad=$(( (BOX_INNER_WIDTH - visible_len) / 2 ))
    right_pad=$(( BOX_INNER_WIDTH - visible_len - left_pad ))

    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad_buf}${C_WHITE}${APP_TITLE} ${C_CYAN}${APP_VERSION}${C_MAGENTA}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}│${C_RESET}"$'\n'

    local tab_line="${C_MAGENTA}│ "
    TAB_ZONES=()

    for (( i = 0; i < TAB_COUNT; i++ )); do
        local name=${TABS[i]}
        len=${#name}
        zone_start=$current_col
        if (( i == CURRENT_TAB )); then
            tab_line+="${C_CYAN}${C_INVERSE} ${name} ${C_RESET}${C_MAGENTA}│ "
        else
            tab_line+="${C_GREY} ${name} ${C_MAGENTA}│ "
        fi
        TAB_ZONES+=("${zone_start}:$(( zone_start + len + 1 ))")
        current_col=$(( current_col + len + 4 ))
    done

    pad_needed=$(( BOX_INNER_WIDTH - current_col + 2 ))
    # FIX: Visual safety - ensure padding is not negative
    (( pad_needed < 0 )) && pad_needed=0
    
    if (( pad_needed > 0 )); then
        printf -v pad_buf '%*s' "$pad_needed" ''
        tab_line+="${pad_buf}"
    fi
    tab_line+="${C_MAGENTA}│${C_RESET}"

    buf+="${tab_line}"$'\n'
    buf+="${C_MAGENTA}└${H_LINE}┘${C_RESET}"$'\n'

    local -n _draw_items_ref="TAB_ITEMS_${CURRENT_TAB}"
    count=${#_draw_items_ref[@]}

    # Scroll Logic
    if (( count == 0 )); then
        SELECTED_ROW=0; SCROLL_OFFSET=0
    else
        (( SELECTED_ROW < 0 )) && SELECTED_ROW=0
        (( SELECTED_ROW >= count )) && SELECTED_ROW=$(( count - 1 ))
        if (( SELECTED_ROW < SCROLL_OFFSET )); then
            SCROLL_OFFSET=$SELECTED_ROW
        elif (( SELECTED_ROW >= SCROLL_OFFSET + MAX_DISPLAY_ROWS )); then
            SCROLL_OFFSET=$(( SELECTED_ROW - MAX_DISPLAY_ROWS + 1 ))
        fi
        local -i max_scroll=$(( count - MAX_DISPLAY_ROWS ))
        (( max_scroll < 0 )) && max_scroll=0
        (( SCROLL_OFFSET > max_scroll )) && SCROLL_OFFSET=$max_scroll
    fi

    visible_start=$SCROLL_OFFSET
    visible_end=$(( SCROLL_OFFSET + MAX_DISPLAY_ROWS ))
    (( visible_end > count )) && visible_end=$count

    if (( SCROLL_OFFSET > 0 )); then
        buf+="${C_GREY}    ▲ (more above)${CLR_EOL}${C_RESET}"$'\n'
    else
        buf+="${CLR_EOL}"$'\n'
    fi

    for (( i = visible_start; i < visible_end; i++ )); do
        item=${_draw_items_ref[i]}
        val=${VALUE_CACHE["${CURRENT_TAB}::${item}"]:-${UNSET_MARKER}}
        config=${ITEM_MAP["${CURRENT_TAB}::${item}"]}
        IFS='|' read -r _ type _ _ _ _ <<< "$config"

        case "$type" in
            menu) display="${C_YELLOW}[+] Open Menu ...${C_RESET}" ;;
            *)
                case "$val" in
                    true)              display="${C_GREEN}ON${C_RESET}" ;;
                    false)             display="${C_RED}OFF${C_RESET}" ;;
                    "$UNSET_MARKER")   display="${C_YELLOW}⚠ UNSET${C_RESET}" ;;
                    *)                 display="${C_WHITE}${val}${C_RESET}" ;;
                esac
                ;;
        esac

        printf -v padded_item "%-${ITEM_PADDING}s" "${item:0:${ITEM_PADDING}}"
        if (( i == SELECTED_ROW )); then
            buf+="${C_CYAN} ➤ ${C_INVERSE}${padded_item}${C_RESET} : ${display}${CLR_EOL}"$'\n'
        else
            buf+="    ${padded_item} : ${display}${CLR_EOL}"$'\n'
        fi
    done

    local -i rows_rendered=$(( visible_end - visible_start ))
    for (( i = rows_rendered; i < MAX_DISPLAY_ROWS; i++ )); do
        buf+="${CLR_EOL}"$'\n'
    done

    if (( count > MAX_DISPLAY_ROWS )); then
        local position_info="[$(( SELECTED_ROW + 1 ))/${count}]"
        if (( visible_end < count )); then
            buf+="${C_GREY}    ▼ (more below) ${position_info}${CLR_EOL}${C_RESET}"$'\n'
        else
            buf+="${C_GREY}                   ${position_info}${CLR_EOL}${C_RESET}"$'\n'
        fi
    else
        buf+="${CLR_EOL}"$'\n'
    fi

    buf+=$'\n'"${C_CYAN} [Tab] Category  [r] Reset  [←/→ h/l] Adjust  [Enter] Action  [q] Quit${C_RESET}"$'\n'
    buf+="${C_CYAN} File: ${C_WHITE}${CONFIG_FILE}${C_RESET}${CLR_EOL}${CLR_EOS}"
    printf '%s' "$buf"
}

draw_detail_view() {
    local buf="" pad_buf="" padded_item="" item val display type config
    local -i i count pad_needed visible_start visible_end
    local -i left_pad right_pad
    
    # 1. Header (Same Height as Main)
    buf+="${CURSOR_HOME}"
    buf+="${C_MAGENTA}┌${H_LINE}┐${C_RESET}${CLR_EOL}"$'\n'
    
    local title=" DETAIL VIEW "
    local sub=" ${CURRENT_MENU_ID} "
    strip_ansi "$title"; local t_len=${#REPLY}
    strip_ansi "$sub"; local s_len=${#REPLY}
    local vis_len=$(( t_len + s_len ))
    left_pad=$(( (BOX_INNER_WIDTH - vis_len) / 2 ))
    right_pad=$(( BOX_INNER_WIDTH - vis_len - left_pad ))

    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad_buf}${C_YELLOW}${title}${C_GREY}${sub}${C_MAGENTA}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}│${C_RESET}${CLR_EOL}"$'\n'
    
    # Breadcrumb line (replaces Tab line)
    local breadcrumb=" « Back to ${TABS[CURRENT_TAB]}"
    strip_ansi "$breadcrumb"; local b_len=${#REPLY}
    pad_needed=$(( BOX_INNER_WIDTH - b_len ))
    # FIX: Visual safety
    (( pad_needed < 0 )) && pad_needed=0
    
    printf -v pad_buf '%*s' "$pad_needed" ''
    
    buf+="${C_MAGENTA}│${C_CYAN}${breadcrumb}${C_RESET}${pad_buf}${C_MAGENTA}│${C_RESET}${CLR_EOL}"$'\n'
    buf+="${C_MAGENTA}└${H_LINE}┘${C_RESET}${CLR_EOL}"$'\n'
    
    # 2. Items (Logic copied from draw_main_view, targeting SUBMENU)
    local -n _detail_items_ref="SUBMENU_ITEMS_${CURRENT_MENU_ID}"
    count=${#_detail_items_ref[@]}

    if (( count == 0 )); then
        SELECTED_ROW=0; SCROLL_OFFSET=0
    else
        (( SELECTED_ROW < 0 )) && SELECTED_ROW=0
        (( SELECTED_ROW >= count )) && SELECTED_ROW=$(( count - 1 ))
        if (( SELECTED_ROW < SCROLL_OFFSET )); then
            SCROLL_OFFSET=$SELECTED_ROW
        elif (( SELECTED_ROW >= SCROLL_OFFSET + MAX_DISPLAY_ROWS )); then
            SCROLL_OFFSET=$(( SELECTED_ROW - MAX_DISPLAY_ROWS + 1 ))
        fi
        local -i max_scroll=$(( count - MAX_DISPLAY_ROWS ))
        (( max_scroll < 0 )) && max_scroll=0
        (( SCROLL_OFFSET > max_scroll )) && SCROLL_OFFSET=$max_scroll
    fi

    visible_start=$SCROLL_OFFSET
    visible_end=$(( SCROLL_OFFSET + MAX_DISPLAY_ROWS ))
    (( visible_end > count )) && visible_end=$count

    # Spacer Row (Matches Main View 'more above' line)
    if (( SCROLL_OFFSET > 0 )); then
        buf+="${C_GREY}    ▲ (more above)${CLR_EOL}${C_RESET}"$'\n'
    else
        buf+="${CLR_EOL}"$'\n'
    fi

    for (( i = visible_start; i < visible_end; i++ )); do
        item=${_detail_items_ref[i]}
        val=${VALUE_CACHE["${CURRENT_MENU_ID}::${item}"]:-${UNSET_MARKER}}
        
        case "$val" in
            true)              display="${C_GREEN}ON${C_RESET}" ;;
            false)             display="${C_RED}OFF${C_RESET}" ;;
            "$UNSET_MARKER")   display="${C_YELLOW}⚠ UNSET${C_RESET}" ;;
            *)                 display="${C_WHITE}${val}${C_RESET}" ;;
        esac

        printf -v padded_item "%-${ITEM_PADDING}s" "${item:0:${ITEM_PADDING}}"
        if (( i == SELECTED_ROW )); then
            buf+="${C_CYAN} ➤ ${C_INVERSE}${padded_item}${C_RESET} : ${display}${CLR_EOL}"$'\n'
        else
            buf+="    ${padded_item} : ${display}${CLR_EOL}"$'\n'
        fi
    done

    # Fill remaining rows
    local -i rows_rendered=$(( visible_end - visible_start ))
    for (( i = rows_rendered; i < MAX_DISPLAY_ROWS; i++ )); do
        buf+="${CLR_EOL}"$'\n'
    done

    # Footer Info
    if (( count > MAX_DISPLAY_ROWS )); then
        local position_info="[$(( SELECTED_ROW + 1 ))/${count}]"
        if (( visible_end < count )); then
            buf+="${C_GREY}    ▼ (more below) ${position_info}${CLR_EOL}${C_RESET}"$'\n'
        else
            buf+="${C_GREY}                   ${position_info}${CLR_EOL}${C_RESET}"$'\n'
        fi
    else
        buf+="${CLR_EOL}"$'\n'
    fi
    
    buf+=$'\n'"${C_CYAN} [Esc] Back  [r] Reset  [←/→ h/l] Adjust  [Enter] Toggle  [q] Quit${C_RESET}${CLR_EOL}"$'\n'
    buf+="${C_CYAN} Submenu: ${C_WHITE}${CURRENT_MENU_ID}${C_RESET}${CLR_EOL}${CLR_EOS}"
    printf '%s' "$buf"
}

draw_ui() {
    case $CURRENT_VIEW in
        0) draw_main_view ;;
        1) draw_detail_view ;;
    esac
}

# --- Input Handling ---

navigate() {
    local -i dir=$1
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _nav_items_ref="$REPLY_REF"
    local -i count=${#_nav_items_ref[@]}
    (( count == 0 )) && return 0
    SELECTED_ROW=$(( (SELECTED_ROW + dir + count) % count ))
}

navigate_page() {
    local -i dir=$1
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _navp_items_ref="$REPLY_REF"
    local -i count=${#_navp_items_ref[@]}
    (( count == 0 )) && return 0
    SELECTED_ROW=$(( SELECTED_ROW + dir * MAX_DISPLAY_ROWS ))
    if (( SELECTED_ROW < 0 )); then SELECTED_ROW=0; fi
    if (( SELECTED_ROW >= count )); then SELECTED_ROW=$(( count - 1 )); fi
}

navigate_end() {
    local -i target=$1
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _nave_items_ref="$REPLY_REF"
    local -i count=${#_nave_items_ref[@]}
    (( count == 0 )) && return 0
    if (( target == 0 )); then SELECTED_ROW=0; else SELECTED_ROW=$(( count - 1 )); fi
}

adjust() {
    local -i dir=$1
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _adj_items_ref="$REPLY_REF"
    (( ${#_adj_items_ref[@]} == 0 )) && return 0
    modify_value "${_adj_items_ref[SELECTED_ROW]}" "$dir"
}

switch_tab() {
    local -i dir=${1:-1}
    CURRENT_TAB=$(( (CURRENT_TAB + dir + TAB_COUNT) % TAB_COUNT ))
    SELECTED_ROW=0
    SCROLL_OFFSET=0
    load_active_values
}

set_tab() {
    local -i idx=$1
    if (( idx != CURRENT_TAB && idx >= 0 && idx < TAB_COUNT )); then
        CURRENT_TAB=$idx
        SELECTED_ROW=0
        SCROLL_OFFSET=0
        load_active_values
    fi
}

check_drilldown() {
    local -n _dd_items_ref="TAB_ITEMS_${CURRENT_TAB}"
    (( ${#_dd_items_ref[@]} == 0 )) && return 1
    
    local item="${_dd_items_ref[SELECTED_ROW]}"
    local config="${ITEM_MAP["${CURRENT_TAB}::${item}"]}"
    local key type
    IFS='|' read -r key type _ _ _ _ <<< "$config"
    
    if [[ "$type" == "menu" ]]; then
        # Save state
        PARENT_ROW=$SELECTED_ROW
        PARENT_SCROLL=$SCROLL_OFFSET
        
        # Switch Context
        CURRENT_MENU_ID="$key"
        CURRENT_VIEW=1
        SELECTED_ROW=0
        SCROLL_OFFSET=0
        load_active_values
        return 0
    fi
    return 1
}

go_back() {
    CURRENT_VIEW=0
    SELECTED_ROW=$PARENT_ROW
    SCROLL_OFFSET=$PARENT_SCROLL
    load_active_values
}

handle_mouse() {
    local input=$1
    # FIX: Remove this line that disabled mouse in submenus
    # if (( CURRENT_VIEW != 0 )); then return 0; fi

    local -i button x y i start end
    local type zone

    local body=${input#'[<'}
    [[ "$body" == "$input" ]] && return 0
    local terminator=${body: -1}
    [[ "$terminator" != "M" && "$terminator" != "m" ]] && return 0
    body=${body%[Mm]}
    local field1 field2 field3
    IFS=';' read -r field1 field2 field3 <<< "$body"
    [[ ! "$field1" =~ ^[0-9]+$ ]] && return 0
    [[ ! "$field2" =~ ^[0-9]+$ ]] && return 0
    [[ ! "$field3" =~ ^[0-9]+$ ]] && return 0
    button=$field1; x=$field2; y=$field3

    if (( button == 64 )); then navigate -1; return 0; fi
    if (( button == 65 )); then navigate 1; return 0; fi
    [[ "$terminator" != "M" ]] && return 0

    if (( y == TAB_ROW )); then
        if (( CURRENT_VIEW == 0 )); then
            for (( i = 0; i < TAB_COUNT; i++ )); do
                zone=${TAB_ZONES[i]}
                start=${zone%%:*}
                end=${zone##*:}
                if (( x >= start && x <= end )); then set_tab "$i"; return 0; fi
            done
        else
            # In detail view, clicking the header/breadcrumb could go back
            # Breadcrumb line is roughly the TAB_ROW
            go_back
            return 0
        fi
    fi

    local -i effective_start=$(( ITEM_START_ROW + 1 ))
    if (( y >= effective_start && y < effective_start + MAX_DISPLAY_ROWS )); then
        local -i clicked_idx=$(( y - effective_start + SCROLL_OFFSET ))
        
        # FIX: Resolve target variable name FIRST, then declare nameref.
        # This prevents the Bash behavior where assigning to an unbound nameref
        # assigns the string to the ref variable itself rather than pointing to the target.
        local _target_var_name
        if (( CURRENT_VIEW == 0 )); then
             _target_var_name="TAB_ITEMS_${CURRENT_TAB}"
        else
             _target_var_name="SUBMENU_ITEMS_${CURRENT_MENU_ID}"
        fi

        local -n _mouse_items_ref="$_target_var_name"
        
        local -i count=${#_mouse_items_ref[@]}
        if (( clicked_idx >= 0 && clicked_idx < count )); then
            SELECTED_ROW=$clicked_idx
            if (( x > ADJUST_THRESHOLD )); then
                if (( button == 0 )); then
                    # Left Click Logic:
                    # If in Main View (0), try check_drilldown first to enter menus.
                    # If check_drilldown fails (not a menu), or we are in a submenu, fall back to adjust 1.
                    if (( CURRENT_VIEW == 0 )); then
                        check_drilldown || adjust 1
                    else
                        adjust 1
                    fi
                else
                    # Right Click Logic
                    adjust -1
                fi
            fi
        fi
    fi
    return 0
}

read_escape_seq() {
    local -n _esc_out=$1
    _esc_out=""
    local char
    if ! IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char; then
        return 1
    fi
    _esc_out+="$char"
    if [[ "$char" == '[' || "$char" == 'O' ]]; then
        while IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char; do
            _esc_out+="$char"
            if [[ "$char" =~ [a-zA-Z~] ]]; then break; fi
        done
    fi
    return 0
}

# --- Input Router ---

handle_key_main() {
    local key="$1"
    case "$key" in
        '[Z')                switch_tab -1; return ;;
        '[A'|'OA')           navigate -1; return ;;
        '[B'|'OB')           navigate 1; return ;;
        '[C'|'OC')           adjust 1; return ;;
        '[D'|'OD')           adjust -1; return ;;
        '[5~')               navigate_page -1; return ;;
        '[6~')               navigate_page 1; return ;;
        '[H'|'[1~')          navigate_end 0; return ;;
        '[F'|'[4~')          navigate_end 1; return ;;
        '['*'<'*[Mm])        handle_mouse "$key"; return ;;
    esac

    case "$key" in
        k|K)            navigate -1 ;;
        j|J)            navigate 1 ;;
        l|L)            adjust 1 ;;
        h|H)            adjust -1 ;;
        g)              navigate_end 0 ;;
        G)              navigate_end 1 ;;
        $'\t')          switch_tab 1 ;;
        r|R)            reset_defaults ;;
        # FIX: Restored Enter key behavior. 
        # If drilldown fails (not a menu), perform 'adjust 1' (toggle/increment).
        ''|$'\n')       check_drilldown || adjust 1 ;;
        q|Q|$'\x03')    exit 0 ;;
    esac
}

handle_key_detail() {
    local key="$1"
    case "$key" in
        '[A'|'OA')           navigate -1; return ;;
        '[B'|'OB')           navigate 1; return ;;
        '[C'|'OC')           adjust 1; return ;;
        '[D'|'OD')           adjust -1; return ;;
        '[5~')               navigate_page -1; return ;;
        '[6~')               navigate_page 1; return ;;
        '[H'|'[1~')          navigate_end 0; return ;;
        '[F'|'[4~')          navigate_end 1; return ;;
        '['*'<'*[Mm])        handle_mouse "$key"; return ;; # FIX: Mouse added
    esac

    case "$key" in
        ESC)
            # Return to Main View
            go_back
            ;;
        k|K)            navigate -1 ;;
        j|J)            navigate 1 ;;
        l|L)            adjust 1 ;;
        h|H)            adjust -1 ;;
        g)              navigate_end 0 ;;
        G)              navigate_end 1 ;;
        r|R)            reset_defaults ;;
        ''|$'\n')       adjust 1 ;; # FIX: Added Enter to toggle
        # FIX: Added Quit keys to detail view
        q|Q|$'\x03')    exit 0 ;;
    esac
}

handle_input_router() {
    local key="$1"
    local escape_seq=""

    if [[ "$key" == $'\x1b' ]]; then
        if read_escape_seq escape_seq; then
            key="$escape_seq"
        else
            key="ESC"
        fi
    fi

    case $CURRENT_VIEW in
        0) handle_key_main "$key" ;;
        1) handle_key_detail "$key" ;;
    esac
}

main() {
    if (( BASH_VERSINFO[0] < 5 )); then log_err "Bash 5.0+ required"; exit 1; fi
    if [[ ! -t 0 ]]; then log_err "TTY required"; exit 1; fi
    if [[ ! -f "$CONFIG_FILE" ]]; then log_err "Config not found"; exit 1; fi

    local _dep
    for _dep in awk sed; do
        if ! command -v "$_dep" &>/dev/null; then
            log_err "Missing dependency: ${_dep}"; exit 1
        fi
    done

    register_items
    populate_config_cache

    ORIGINAL_STTY=$(stty -g 2>/dev/null) || ORIGINAL_STTY=""
    stty -icanon -echo min 1 time 0 2>/dev/null

    printf '%s%s%s%s' "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"
    load_active_values

    local key
    while true; do
        draw_ui
        IFS= read -rsn1 key || break
        handle_input_router "$key"
    done
}

main "$@"
