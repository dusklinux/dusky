#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Dusky Appearances - Elite Edition v7.4.0
# -----------------------------------------------------------------------------
# Target: Arch Linux / Hyprland / UWSM
# Description: Tabbed TUI to modify hyprland appearance.conf.
# -----------------------------------------------------------------------------
# KNOWN EDGE CASES & FIXES:
#
# 1. "Locale Bomb" - Non-US locales make awk output "0,5" instead of "0.5"
#    FIX: export LC_NUMERIC=C at script start
#
# 2. "Nested Block Range Trap" - sed range /block/,/}/ stops at ANY }, breaking
#    nested configs like: decoration { blur { ... } shadow_color = x }
#    FIX: Count braces to find exact block boundaries
#
# 3. "First Block Trap" - Config files can have MULTIPLE blocks with the
#    same name. The old code used `head -n1` to get only the first block.
#    FIX: Iterate through ALL block instances and find the one containing the key
#
# 4. "Hyphen Pattern Trap" - Keys like `tap-to-click` need hyphen escaping in
#    sed patterns, otherwise `-` can be misinterpreted in character classes.
#    FIX: Escape hyphens in escape_sed_pattern()
#
# 5. "Silent Failure Trap" - If a key isn't found, old code silently did nothing.
#    FIX: Return status from write functions, show "⚠ UNSET" in UI
# -----------------------------------------------------------------------------

set -euo pipefail

# CRITICAL: Force standard C locale for numeric operations. 
# This prevents awk from outputting commas (0,5) in non-US locales, 
# which would corrupt the Hyprland config.
export LC_NUMERIC=C

# --- Configuration ---
readonly CONFIG_FILE="${HOME}/.config/hypr/edit_here/source/appearance.conf"
readonly APP_TITLE="Dusky Appearances"
readonly APP_VERSION="v7.4.0"

# UI Layout Constants
declare -ri MAX_DISPLAY_ROWS=12
declare -ri BOX_INNER_WIDTH=64
declare -ri ITEM_START_ROW=5
declare -ri ADJUST_THRESHOLD=30
declare -ri ITEM_PADDING=22

# --- Pre-computed Constants ---
declare _H_LINE_BUF
printf -v _H_LINE_BUF '%*s' "$BOX_INNER_WIDTH" ''
readonly H_LINE=${_H_LINE_BUF// /─}

# Timeout for escape sequence reads
readonly ESC_READ_TIMEOUT=0.02

# Internal marker for unset values (uses Unicode to avoid collision with real values)
readonly UNSET_MARKER='«unset»'

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

# --- State ---
declare -i SELECTED_ROW=0
declare -i CURRENT_TAB=0
declare -i SCROLL_OFFSET=0
readonly -a TABS=("Layout" "Decoration" "Blur" "Shadow" "Snap")
declare -ri TAB_COUNT=${#TABS[@]}
declare -a TAB_ZONES=()
declare ORIGINAL_STTY=""

# --- Data Structures ---
declare -A ITEM_MAP=()
declare -A VALUE_CACHE=()
declare -A CONFIG_CACHE=()
declare -A DEFAULTS=()
declare -a TAB_ITEMS_0=() TAB_ITEMS_1=() TAB_ITEMS_2=() TAB_ITEMS_3=() TAB_ITEMS_4=()

# --- Helpers ---

log_err() {
    printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$1" >&2
}

log_warn() {
    printf '%s[WARN]%s %s\n' "$C_MAGENTA" "$C_RESET" "$1" >&2
}

cleanup() {
    printf '%s%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" "$C_RESET"
    
    # Restore terminal state if saved
    if [[ -n "${ORIGINAL_STTY:-}" ]]; then
        stty "$ORIGINAL_STTY" 2>/dev/null || :
    fi
    
    printf '\n'
}

# Escape special characters for sed REPLACEMENT string
escape_sed_replacement() {
    local _s=$1
    local -n _out=$2
    _s=${_s//\\/\\\\}
    _s=${_s//|/\\|}
    _s=${_s//&/\\&}
    _s=${_s//$'\n'/\\$'\n'}
    _out=$_s
}

# Escape special characters for sed PATTERN (BRE)
escape_sed_pattern() {
    local _s=$1
    local -n _out=$2
    _s=${_s//\\/\\\\}
    _s=${_s//./\\.}
    _s=${_s//\*/\\*}
    _s=${_s//\[/\\[}
    _s=${_s//^/\\^}
    _s=${_s//\$/\\\$}
    _s=${_s//|/\\|}
    _s=${_s//-/\\-}      # FIX: "Hyphen Pattern Trap" - escape hyphens
    _out=$_s
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# --- Registration ---

register() {
    local -i tab_idx=$1
    local label=$2 config=$3

    if (( tab_idx < 0 || tab_idx >= TAB_COUNT )); then
        printf '%s[FATAL]%s Invalid tab index %d for "%s"\n' \
            "$C_RED" "$C_RESET" "$tab_idx" "$label" >&2
        exit 1
    fi

    ITEM_MAP["$label"]=$config
    local -n tab_ref="TAB_ITEMS_${tab_idx}"
    tab_ref+=("$label")
}

# --- DEFINITIONS ---

# Tab 0: Layout & General
register 0 "Gaps In"            "gaps_in|int||0|100|1"
register 0 "Gaps Out"           "gaps_out|int||0|100|1"
register 0 "Gaps Workspaces"    "gaps_workspaces|int|general|0|100|1"
register 0 "Border Size"        "border_size|int||0|10|1"
register 0 "Resize on Border"   "resize_on_border|bool|general|||"
register 0 "Allow Tearing"      "allow_tearing|bool|general|||"

# Tab 1: Decoration
register 1 "Rounding"           "rounding|int||0|30|1"
register 1 "Rounding Power"     "rounding_power|float||0.0|10.0|0.1"
register 1 "Active Opacity"     "active_opacity|float||0.1|1.0|0.05"
register 1 "Inactive Opacity"   "inactive_opacity|float||0.1|1.0|0.05"
register 1 "Fullscreen Opacity" "fullscreen_opacity|float||0.1|1.0|0.05"
register 1 "Dim Inactive"       "dim_inactive|bool||||"
register 1 "Dim Strength"       "dim_strength|float||0.0|1.0|0.05"
register 1 "Dim Special"        "dim_special|float||0.0|1.0|0.05"

# Tab 2: Blur
register 2 "Blur Enabled"       "enabled|bool|blur|||"
register 2 "Blur Size"          "size|int|blur|1|20|1"
register 2 "Blur Passes"        "passes|int|blur|1|10|1"
register 2 "Blur Xray"          "xray|bool|blur|||"
register 2 "Blur Noise"         "noise|float|blur|0.0|1.0|0.01"
register 2 "Blur Contrast"      "contrast|float|blur|0.0|2.0|0.05"
register 2 "Blur Brightness"    "brightness|float|blur|0.0|2.0|0.05"
register 2 "Blur Popups"        "popups|bool|blur|||"
register 2 "Blur Vibrancy"      "vibrancy|float|blur|0.0|1.0|0.05"

# Tab 3: Shadow
register 3 "Shadow Enabled"     "enabled|bool|shadow|||"
register 3 "Shadow Range"       "range|int|shadow|0|100|1"
register 3 "Shadow Power"       "render_power|int|shadow|1|4|1"
register 3 "Shadow Sharp"       "sharp|bool|shadow|||"
register 3 "Shadow Scale"       "scale|float|shadow|0.0|1.1|0.05"
register 3 "Shadow Ignore Win"  "ignore_window|bool|shadow|||"
register 3 "Shadow Color"       'color|cycle|shadow|rgba(1a1a1aee),$primary||'

# Tab 4: Snap
register 4 "Snap Enabled"       "enabled|bool|snap|||"
register 4 "Snap Window Gap"    "window_gap|int|snap|0|50|1"
register 4 "Snap Monitor Gap"   "monitor_gap|int|snap|0|50|1"
register 4 "Snap Border Overlap" "border_overlap|bool|snap|||"

# --- DEFAULTS ---
DEFAULTS=(
    ["Gaps In"]=6
    ["Gaps Out"]=12
    ["Gaps Workspaces"]=0
    ["Border Size"]=2
    ["Resize on Border"]=false
    ["Allow Tearing"]=true
    ["Rounding"]=6
    ["Rounding Power"]=6.0
    ["Active Opacity"]=1.0
    ["Inactive Opacity"]=1.0
    ["Fullscreen Opacity"]=1.0
    ["Dim Inactive"]=true
    ["Dim Strength"]=0.2
    ["Dim Special"]=0.8
    ["Blur Enabled"]=false
    ["Blur Size"]=4
    ["Blur Passes"]=2
    ["Blur Xray"]=false
    ["Blur Noise"]=0.0117
    ["Blur Contrast"]=0.8916
    ["Blur Brightness"]=0.8172
    ["Blur Popups"]=false
    ["Blur Vibrancy"]=0.1696
    ["Shadow Enabled"]=false
    ["Shadow Range"]=35
    ["Shadow Power"]=2
    ["Shadow Sharp"]=false
    ["Shadow Scale"]=1.0
    ["Shadow Ignore Win"]=true
    ["Shadow Color"]='rgba(1a1a1aee)'
    ["Snap Enabled"]=false
    ["Snap Window Gap"]=10
    ["Snap Monitor Gap"]=10
    ["Snap Border Overlap"]=false
)

# --- Core Logic ---

populate_config_cache() {
    CONFIG_CACHE=()
    local key_part value_part key_name

    # FIX: Added `|| [[ -n $key_part ]]` for EOF safety
    while IFS='=' read -r key_part value_part || [[ -n $key_part ]]; do
        [[ -z $key_part ]] && continue
        CONFIG_CACHE["$key_part"]=$value_part

        key_name=${key_part%%|*}
        if [[ -z ${CONFIG_CACHE["$key_name|"]:-} ]]; then
            CONFIG_CACHE["$key_name|"]=$value_part
        fi
    done < <(awk '
        BEGIN { depth = 0 }
        /^[[:space:]]*#/ { next }
        {
            line = $0
            sub(/#.*/, "", line)

            if (match(line, /[a-zA-Z0-9_.:-]+[[:space:]]*\{/)) {
                block_str = substr(line, RSTART, RLENGTH)
                sub(/[[:space:]]*\{/, "", block_str)
                depth++
                block_stack[depth] = block_str
            }

            if (line ~ /=/) {
                eq_pos = index(line, "=")
                if (eq_pos > 0) {
                    key = substr(line, 1, eq_pos - 1)
                    val = substr(line, eq_pos + 1)
                    gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
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

# -----------------------------------------------------------------------------
# write_value_to_file - Write a value to the config file
# -----------------------------------------------------------------------------
# CRITICAL: This function handles the "First Block Trap"
#
# Problem: Config files can have MULTIPLE blocks with the same name:
#   input { kb_layout = us }      # Block 1
#   input { sensitivity = 0 }     # Block 2  
#
# The old code used `grep ... | head -n1` which only found Block 1.
# If we tried to modify "sensitivity", it would search Block 1, not find it,
# and silently fail.
#
# Solution: Iterate through ALL blocks with the matching name, check which one
# contains our key, and only modify that specific block.
# -----------------------------------------------------------------------------
write_value_to_file() {
    local key=$1 new_val=$2 block=${3:-}
    
    local current_val=${CONFIG_CACHE["$key|$block"]:-}
    
    # Dirty check: skip write if value unchanged
    [[ "$current_val" == "$new_val" ]] && return 0

    local safe_val safe_key
    escape_sed_replacement "$new_val" safe_val
    escape_sed_pattern "$key" safe_key

    if [[ -n $block ]]; then
        local safe_block
        escape_sed_pattern "$block" safe_block
        
        # CRITICAL FIX: The "First Block Trap"
        # Iterate through ALL block instances, not just the first one.
        # For each block, check if it contains our key before modifying.
        
        local line_num block_start block_end found=0
        
        while IFS=: read -r line_num _; do
            block_start=$line_num
            
            # CRITICAL FIX: The "Nested Block Range Trap"
            # Count braces to find exact block boundaries instead of stopping at first }
            block_end=$(tail -n "+${block_start}" "$CONFIG_FILE" | awk '
                BEGIN { depth = 0; started = 0 }
                {
                    txt = $0
                    sub(/#.*/, "", txt)  # Remove comments to avoid false brace counts
                    
                    n_open = gsub(/{/, "&", txt)
                    n_close = gsub(/}/, "&", txt)
                    
                    if (NR == 1) {
                        depth = n_open
                        started = 1
                    } else {
                        depth += n_open - n_close
                    }
                    
                    if (started && depth <= 0) {
                        print NR
                        exit
                    }
                }
            ')
            
            # Skip if we couldn't determine block end
            [[ -z $block_end ]] && continue
            
            local -i real_end=$(( block_start + block_end - 1 ))
            
            # Check if THIS specific block instance contains our key
            # This is the key fix for multiple same-name blocks
            if sed -n "${block_start},${real_end}p" "$CONFIG_FILE" | \
               grep -q "^[[:space:]]*${safe_key}[[:space:]]*="; then
                
                # Found it! Apply substitution ONLY to this block range
                sed --follow-symlinks -i \
                    "${block_start},${real_end}s|^\([[:space:]]*${safe_key}[[:space:]]*=[[:space:]]*\)[^#]*|\1${safe_val} |" \
                    "$CONFIG_FILE"
                found=1
                break
            fi
        done < <(grep -n "^[[:space:]]*${safe_block}[[:space:]]*{" "$CONFIG_FILE")
        
        # FIX: "Silent Failure Trap" - Return error if key not found
        if (( found == 0 )); then
            return 1
        fi
    else
        # Global key update (no block context)
        sed --follow-symlinks -i \
            "s|^\([[:space:]]*${safe_key}[[:space:]]*=[[:space:]]*\)[^#]*|\1${safe_val} |" \
            "$CONFIG_FILE"
    fi

    # Update cache on success
    CONFIG_CACHE["$key|$block"]=$new_val
    if [[ -z $block ]]; then
        CONFIG_CACHE["$key|"]=$new_val
    fi
    
    return 0
}

load_tab_values() {
    local -n items_ref="TAB_ITEMS_${CURRENT_TAB}"
    local item key type block val

    for item in "${items_ref[@]}"; do
        IFS='|' read -r key type block _ _ _ <<< "${ITEM_MAP[$item]}"
        
        # Try exact match first (key|block)
        val=${CONFIG_CACHE["$key|$block"]:-}
        
        # If empty and no specific block required, try blockless lookup
        if [[ -z $val && -z $block ]]; then
            val=${CONFIG_CACHE["$key|"]:-}
        fi
        
        # FIX: Use distinct marker so user knows value wasn't detected
        if [[ -z $val ]]; then
            VALUE_CACHE["$item"]=$UNSET_MARKER
        else
            VALUE_CACHE["$item"]=$val
        fi
    done
}

modify_value() {
    local label=$1
    local -i direction=$2
    local key type block min max step current new_val

    IFS='|' read -r key type block min max step <<< "${ITEM_MAP[$label]}"
    current=${VALUE_CACHE[$label]:-}
    
    # Handle unset values - use default or sensible minimum
    if [[ $current == "$UNSET_MARKER" || -z $current ]]; then
        current=${DEFAULTS[$label]:-}
        [[ -z $current ]] && current=${min:-0}
    fi

    case $type in
        int)
            if [[ ! $current =~ ^-?[0-9]+$ ]]; then current=${min:-0}; fi
            local -i int_step=${step:-1} int_val=$current
            (( int_val += direction * int_step )) || :
            
            if [[ -n $min ]] && (( int_val < min )); then int_val=$min; fi
            if [[ -n $max ]] && (( int_val > max )); then int_val=$max; fi
            new_val=$int_val
            ;;
        float)
            if [[ ! $current =~ ^-?[0-9]*\.?[0-9]+$ ]]; then current=${min:-0.0}; fi
            new_val=$(awk -v c="$current" -v dir="$direction" -v s="${step:-0.1}" \
                          -v mn="$min" -v mx="$max" 'BEGIN {
                val = c + (dir * s)
                if (mn != "" && val < mn) val = mn
                if (mx != "" && val > mx) val = mx
                printf "%.4g", val
            }')
            ;;
        bool)
            [[ $current == "true" ]] && new_val="false" || new_val="true"
            ;;
        cycle)
            local options_str=$min
            IFS=',' read -r -a opts <<< "$options_str"
            local -i idx=0 found=0 count=${#opts[@]}
            
            for (( i=0; i<count; i++ )); do
                [[ "${opts[i]}" == "$current" ]] && { idx=$i; found=1; break; }
            done
            
            [[ $found -eq 0 ]] && idx=0
            (( idx += direction )) || :
            if (( idx < 0 )); then idx=$(( count - 1 )); fi
            if (( idx >= count )); then idx=0; fi
            new_val=${opts[idx]}
            ;;
        *) return 0 ;;
    esac

    # FIX: Only update cache if write succeeded
    if write_value_to_file "$key" "$new_val" "$block"; then
        VALUE_CACHE["$label"]=$new_val
    fi
}

set_absolute_value() {
    local label=$1 new_val=$2
    local key type block

    IFS='|' read -r key type block _ _ _ <<< "${ITEM_MAP[$label]}"

    # FIX: Only update cache if write succeeded
    if write_value_to_file "$key" "$new_val" "$block"; then
        VALUE_CACHE["$label"]=$new_val
    fi
}

reset_defaults() {
    local -n items_ref="TAB_ITEMS_${CURRENT_TAB}"
    local item def_val

    for item in "${items_ref[@]}"; do
        def_val=${DEFAULTS[$item]:-}
        [[ -n $def_val ]] && set_absolute_value "$item" "$def_val"
    done
}

# --- UI Rendering ---

draw_ui() {
    local buf="" pad_buf="" padded_item=""
    local -i i current_col=3

    buf+="${CURSOR_HOME}"
    buf+="${C_MAGENTA}┌${H_LINE}┐${C_RESET}"$'\n'

    # Header - Dynamic Centering
    local -i visible_len=$(( ${#APP_TITLE} + ${#APP_VERSION} + 1 ))
    local -i left_pad=$(( (BOX_INNER_WIDTH - visible_len) / 2 ))
    local -i right_pad=$(( BOX_INNER_WIDTH - visible_len - left_pad ))

    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad_buf}${C_WHITE}${APP_TITLE} ${C_CYAN}${APP_VERSION}${C_MAGENTA}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}│${C_RESET}"$'\n'

    # Tab bar
    local tab_line="${C_MAGENTA}│ "
    TAB_ZONES=()

    for (( i = 0; i < TAB_COUNT; i++ )); do
        local name=${TABS[i]}
        local -i len=${#name}
        local -i zone_start=$current_col

        if (( i == CURRENT_TAB )); then
            tab_line+="${C_CYAN}${C_INVERSE} ${name} ${C_RESET}${C_MAGENTA}│ "
        else
            tab_line+="${C_GREY} ${name} ${C_MAGENTA}│ "
        fi

        TAB_ZONES+=("${zone_start}:$(( zone_start + len + 1 ))")
        (( current_col += len + 4 )) || :
    done

    local -i pad_needed=$(( BOX_INNER_WIDTH - current_col + 2 ))
    if (( pad_needed > 0 )); then
        printf -v pad_buf '%*s' "$pad_needed" ''
        tab_line+="${pad_buf}"
    fi
    tab_line+="${C_MAGENTA}│${C_RESET}"

    buf+="${tab_line}"$'\n'
    buf+="${C_MAGENTA}└${H_LINE}┘${C_RESET}"$'\n'

    # Items with scroll support
    local -n items_ref="TAB_ITEMS_${CURRENT_TAB}"
    local -i count=${#items_ref[@]}
    local item val display

    # Bounds & scroll calculation
    if (( count == 0 )); then
        SELECTED_ROW=0
        SCROLL_OFFSET=0
    else
        (( SELECTED_ROW < 0 )) && SELECTED_ROW=0
        (( SELECTED_ROW >= count )) && SELECTED_ROW=$(( count - 1 ))

        # Auto-scroll to keep selection visible
        if (( SELECTED_ROW < SCROLL_OFFSET )); then
            SCROLL_OFFSET=$SELECTED_ROW
        elif (( SELECTED_ROW >= SCROLL_OFFSET + MAX_DISPLAY_ROWS )); then
            SCROLL_OFFSET=$(( SELECTED_ROW - MAX_DISPLAY_ROWS + 1 ))
        fi

        # Clamp scroll offset
        (( SCROLL_OFFSET < 0 )) && SCROLL_OFFSET=0
        local -i max_scroll=$(( count - MAX_DISPLAY_ROWS ))
        (( max_scroll < 0 )) && max_scroll=0
        (( SCROLL_OFFSET > max_scroll )) && SCROLL_OFFSET=$max_scroll
    fi

    local -i visible_start=$SCROLL_OFFSET
    local -i visible_end=$(( SCROLL_OFFSET + MAX_DISPLAY_ROWS ))
    (( visible_end > count )) && visible_end=$count

    # Top scroll indicator
    if (( SCROLL_OFFSET > 0 )); then
        buf+="${C_GREY}    ▲ (more above)${CLR_EOL}${C_RESET}"$'\n'
    else
        buf+="${CLR_EOL}"$'\n'
    fi

    # Render visible items
    for (( i = visible_start; i < visible_end; i++ )); do
        item=${items_ref[i]}
        val=${VALUE_CACHE[$item]:-$UNSET_MARKER}

        # FIX: Use distinct yellow warning for unset values
        case $val in
            true)              display="${C_GREEN}ON${C_RESET}" ;;
            false)             display="${C_RED}OFF${C_RESET}" ;;
            "$UNSET_MARKER")   display="${C_YELLOW}⚠ UNSET${C_RESET}" ;;
            *'$primary'*)      display="${C_MAGENTA}Dynamic${C_RESET}" ;;
            *)                 display="${C_WHITE}${val}${C_RESET}" ;;
        esac

        printf -v padded_item "%-${ITEM_PADDING}s" "${item:0:$ITEM_PADDING}"

        if (( i == SELECTED_ROW )); then
            buf+="${C_CYAN} ➤ ${C_INVERSE}${padded_item}${C_RESET} : ${display}${CLR_EOL}"$'\n'
        else
            buf+="    ${padded_item} : ${display}${CLR_EOL}"$'\n'
        fi
    done

    # Pad remaining rows
    local -i rows_rendered=$(( visible_end - visible_start ))
    for (( i = rows_rendered; i < MAX_DISPLAY_ROWS; i++ )); do
        buf+="${CLR_EOL}"$'\n'
    done

    # Bottom scroll indicator with position counter
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

    buf+=$'\n'"${C_CYAN} [Tab] Category  [r] Reset  [←/→ h/l] Adjust  [↑/↓ j/k] Nav  [q] Quit${C_RESET}"$'\n'
    buf+="${C_CYAN} File: ${C_WHITE}${CONFIG_FILE}${C_RESET}${CLR_EOL}${CLR_EOS}"

    printf '%s' "$buf"
}

# --- Input Handling ---

navigate() {
    local -i dir=$1
    local -n items_ref="TAB_ITEMS_${CURRENT_TAB}"
    local -i count=${#items_ref[@]}

    (( count == 0 )) && return 0
    (( SELECTED_ROW += dir )) || :

    # Wrap selection
    if (( SELECTED_ROW < 0 )); then
        SELECTED_ROW=$(( count - 1 ))
    elif (( SELECTED_ROW >= count )); then
        SELECTED_ROW=0
    fi
}

# Page navigation (no wrap)
navigate_page() {
    local -i dir=$1
    local -n items_ref="TAB_ITEMS_${CURRENT_TAB}"
    local -i count=${#items_ref[@]}

    (( count == 0 )) && return 0
    (( SELECTED_ROW += dir * MAX_DISPLAY_ROWS )) || :

    # Clamp without wrapping
    (( SELECTED_ROW < 0 )) && SELECTED_ROW=0
    (( SELECTED_ROW >= count )) && SELECTED_ROW=$(( count - 1 ))
}

# Jump to first/last item
navigate_end() {
    local -i target=$1  # 0 = first, 1 = last
    local -n items_ref="TAB_ITEMS_${CURRENT_TAB}"
    local -i count=${#items_ref[@]}

    (( count == 0 )) && return 0

    if (( target == 0 )); then
        SELECTED_ROW=0
    else
        SELECTED_ROW=$(( count - 1 ))
    fi
}

adjust() {
    local -i dir=$1
    local -n items_ref="TAB_ITEMS_${CURRENT_TAB}"

    (( ${#items_ref[@]} == 0 )) && return 0
    modify_value "${items_ref[SELECTED_ROW]}" "$dir"
}

switch_tab() {
    local -i dir=${1:-1}

    (( CURRENT_TAB += dir )) || :

    if (( CURRENT_TAB >= TAB_COUNT )); then
        CURRENT_TAB=0
    elif (( CURRENT_TAB < 0 )); then
        CURRENT_TAB=$(( TAB_COUNT - 1 ))
    fi

    SELECTED_ROW=0
    SCROLL_OFFSET=0
    load_tab_values
}

set_tab() {
    local -i idx=$1

    if (( idx != CURRENT_TAB && idx >= 0 && idx < TAB_COUNT )); then
        CURRENT_TAB=$idx
        SELECTED_ROW=0
        SCROLL_OFFSET=0
        load_tab_values
    fi
}

handle_mouse() {
    local input=$1
    local -i button x y i
    local type zone start end

    # SGR Mouse Mode (1006)
    if [[ $input =~ ^\[<([0-9]+)\;([0-9]+)\;([0-9]+)([Mm])$ ]]; then
        button=${BASH_REMATCH[1]}
        x=${BASH_REMATCH[2]}
        y=${BASH_REMATCH[3]}
        type=${BASH_REMATCH[4]}

        # Handle scroll wheel (button 64=up, 65=down) on any event
        if (( button == 64 )); then
            navigate -1
            return 0
        elif (( button == 65 )); then
            navigate 1
            return 0
        fi

        # Only handle Button Press ('M'), ignore Release ('m') for clicks
        [[ $type != "M" ]] && return 0

        # Tab bar click detection (Row 3)
        if (( y == 3 )); then
            for (( i = 0; i < TAB_COUNT; i++ )); do
                zone=${TAB_ZONES[i]}
                start=${zone%%:*}
                end=${zone##*:}
                if (( x >= start && x <= end )); then
                    set_tab "$i"
                    return 0
                fi
            done
        fi

        # Item click detection (accounting for scroll indicator offset)
        local -n items_ref="TAB_ITEMS_${CURRENT_TAB}"
        local -i count=${#items_ref[@]}
        local -i item_row_start=$(( ITEM_START_ROW + 1 ))

        if (( y >= item_row_start && y < item_row_start + MAX_DISPLAY_ROWS )); then
            local -i clicked_idx=$(( y - item_row_start + SCROLL_OFFSET ))
            if (( clicked_idx >= 0 && clicked_idx < count )); then
                SELECTED_ROW=$clicked_idx
                if (( x > ADJUST_THRESHOLD )); then
                    (( button == 0 )) && adjust 1 || adjust -1
                fi
            fi
        fi
    fi
}

# --- Main ---

main() {
    # Bash Version Check (namerefs require 4.3+)
    if (( BASH_VERSINFO[0] < 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] < 3) )); then
        printf '%s[FATAL]%s Bash 4.3+ required (found %s)\n' \
               "$C_RED" "$C_RESET" "$BASH_VERSION" >&2
        exit 1
    fi

    # Config Validation
    [[ ! -f $CONFIG_FILE ]] && { log_err "Config not found: $CONFIG_FILE"; exit 1; }
    [[ ! -r $CONFIG_FILE ]] && { log_err "Config not readable: $CONFIG_FILE"; exit 1; }
    [[ ! -w $CONFIG_FILE ]] && { log_err "Config not writable: $CONFIG_FILE"; exit 1; }

    # Dependency Check
    command -v awk &>/dev/null || { log_err "Required: awk"; exit 1; }
    command -v sed &>/dev/null || { log_err "Required: sed"; exit 1; }

    # Initialization
    populate_config_cache

    # Save terminal state
    if command -v stty &>/dev/null; then
        ORIGINAL_STTY=$(stty -g 2>/dev/null) || ORIGINAL_STTY=""
    fi

    printf '%s%s%s%s' "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"
    load_tab_values

    local key seq char

    # Event Loop
    while true; do
        draw_ui

        # Safety: break on EOF to prevent 100% CPU loops
        IFS= read -rsn1 key || break

        if [[ $key == $'\x1b' ]]; then
            seq=""
            while IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char; do
                seq+="$char"
            done

            case $seq in
                '[Z')          switch_tab -1 ;;     # Shift+Tab
                '[A'|'OA')     navigate -1 ;;       # Arrow Up
                '[B'|'OB')     navigate 1 ;;        # Arrow Down
                '[C'|'OC')     adjust 1 ;;          # Arrow Right
                '[D'|'OD')     adjust -1 ;;         # Arrow Left
                '[5~')         navigate_page -1 ;;  # Page Up
                '[6~')         navigate_page 1 ;;   # Page Down
                '[H'|'[1~')    navigate_end 0 ;;    # Home
                '[F'|'[4~')    navigate_end 1 ;;    # End
                '['*'<'*)      handle_mouse "$seq" ;;
            esac
        else
            case $key in
                k|K)           navigate -1 ;;
                j|J)           navigate 1 ;;
                l|L)           adjust 1 ;;
                h|H)           adjust -1 ;;
                g)             navigate_end 0 ;;    # vim: go to top
                G)             navigate_end 1 ;;    # vim: go to bottom
                $'\t')         switch_tab 1 ;;
                r|R)           reset_defaults ;;
                q|Q|$'\x03')   break ;;
            esac
        fi
    done
}

main "$@"
