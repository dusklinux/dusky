#!/usr/bin/env bash
# Shared Dusky Bash TUI frontend, derived from the standalone v5.9.1 template.
# Sourcing installs no traps and opens no config or terminal; tui_main starts it.

if [[ ${DUSKY_BASH_FRONTEND_LOADED:-0} == 1 ]]; then return 0; fi
if (( BASH_VERSINFO[0] < 5 ||
      (BASH_VERSINFO[0] == 5 && BASH_VERSINFO[1] < 3) ||
      (BASH_VERSINFO[0] == 5 && BASH_VERSINFO[1] == 3 && BASH_VERSINFO[2] < 20) )); then
    printf 'Dusky TUI requires Bash 5.3.20+\n' >&2
    return 1
fi
declare DUSKY_BASH_FRONTEND_DIR=${BASH_SOURCE[0]%/*}
[[ ${BASH_SOURCE[0]} == */* ]] || DUSKY_BASH_FRONTEND_DIR=.
# shellcheck source=core_types.sh
source "$DUSKY_BASH_FRONTEND_DIR/core_types.sh" || return 1
unset DUSKY_BASH_FRONTEND_DIR
declare -gr DUSKY_BASH_FRONTEND_LOADED=1

tui_init() {
    shopt -s extglob
    : "${APP_TITLE:=Dusky Configuration Editor}" "${APP_VERSION:=v1.0}"
    : "${MAX_DISPLAY_ROWS:=14}" "${BOX_INNER_WIDTH:=76}"
    : "${ADJUST_THRESHOLD:=38}" "${ITEM_PADDING:=32}"
    declare -gi HEADER_ROWS=4 TAB_ROW=3 ITEM_START_ROW=5
    declare -g _h_line_buf
    printf -v _h_line_buf '%*s' "$BOX_INNER_WIDTH" '' || true
    declare -g H_LINE="${_h_line_buf// /─}"
    unset _h_line_buf

    # ANSI constants.
    declare -g C_RESET=$'\033[0m'
    declare -g C_CYAN=$'\033[1;36m'
    declare -g C_GREEN=$'\033[1;32m'
    declare -g C_MAGENTA=$'\033[1;35m'
    declare -g C_RED=$'\033[1;31m'
    declare -g C_YELLOW=$'\033[1;33m'
    declare -g C_WHITE=$'\033[1;37m'
    declare -g C_GREY=$'\033[1;30m'
    declare -g C_INVERSE=$'\033[7m'
    declare -g CLR_EOL=$'\033[K'
    declare -g CLR_EOS=$'\033[J'
    declare -g CLR_SCREEN=$'\033[2J'
    declare -g CURSOR_HOME=$'\033[H'
    declare -g CURSOR_HIDE=$'\033[?25l'
    declare -g CURSOR_SHOW=$'\033[?25h'
    declare -g ALT_SCREEN_ON=$'\033[?1049h'
    declare -g ALT_SCREEN_OFF=$'\033[?1049l'
    # Basic clicks first, then button-motion only (1002), never all-motion (1003).
    # Unsupported 1002 leaves basic clicks working; SGR motion code 32 means left held.
    declare -g MOUSE_ON=$'\033[?1000h\033[?1002h\033[?1006h\033[?2004h'
    declare -g MOUSE_OFF=$'\033[?1000l\033[?1002l\033[?1006l\033[?2004l'

    declare -g ESC_READ_TIMEOUT=0.08
    declare -g READ_LOOP_TIMEOUT=0.25
    declare -gi MAX_ESCAPE_BYTES=64
    declare -g UNSET_MARKER='«unset»'

    declare -gi SELECTED_ROW=0 CURRENT_TAB=0 SCROLL_OFFSET=0
    declare -gi TAB_COUNT=${#TABS[@]}
    declare -ga TAB_ZONES=()
    declare -gi TAB_SCROLL_START=0
    declare -g ORIGINAL_STTY=""
    declare -gi TUI_STARTED=0

    declare -ga TAB_SAVED_ROW=()
    declare -ga TAB_SAVED_SCROLL=()
    for (( _ti = 0; _ti < TAB_COUNT; _ti++ )); do
        TAB_SAVED_ROW+=("0")
        TAB_SAVED_SCROLL+=("0")
    done
    unset _ti

    declare -gi CURRENT_VIEW=0
    declare -g CURRENT_MENU_ID=""
    declare -gi PARENT_ROW=0 PARENT_SCROLL=0
    declare -gi PICKER_PARENT_VIEW=0 PICKER_PARENT_ROW=0 PICKER_PARENT_SCROLL=0
    declare -gi RESIZE_PENDING=0 PASTE_ACTIVE=0
    declare -gi MOUSE_CLICK_PENDING=0 MOUSE_PRESS_X=0 MOUSE_PRESS_Y=0
    declare -g MOUSE_PRESS_CONTEXT=""
    declare -g PASTE_TAIL=""

    declare -g PICKER_TITLE=""
    declare -ga PICKER_ITEMS=()
    declare -ga PICKER_HINTS=()
    declare -g PICKER_CALLBACK=""
    declare -gi PICKER_SELECTED=0 PICKER_SCROLL=0


    declare -gi TERM_ROWS=0 TERM_COLS=0
    declare -gi MIN_TERM_COLS=$(( BOX_INNER_WIDTH + 2 ))
    declare -gi MIN_TERM_ROWS=$(( HEADER_ROWS + MAX_DISPLAY_ROWS + 6 ))

    declare -gi ENGINE_CHANGED=0
    declare -g STATUS_MESSAGE=""
    declare -g LEFT_ARROW_ZONE=""
    declare -g RIGHT_ARROW_ZONE=""

    declare -gA ITEM_MAP=()
    declare -gA VALUE_CACHE=()
    declare -gA DEFAULTS=()

    for (( _ti = 0; _ti < TAB_COUNT; _ti++ )); do
        declare -ga "TAB_ITEMS_${_ti}=()"
    done
    unset _ti
}

log_err() {
    printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$1" >&2 || true
}

set_status() { declare -g STATUS_MESSAGE=${1//[[:cntrl:]]/?}; }
clear_status() { declare -g STATUS_MESSAGE=""; }

cleanup() {
    if [[ -t 1 ]]; then
        if (( TUI_STARTED )); then
            printf '%s%s%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" "$C_RESET" "$ALT_SCREEN_OFF" 2>/dev/null || :
        elif [[ -n ${ORIGINAL_STTY:-} ]]; then
            printf '%s%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" "$C_RESET" 2>/dev/null || :
        fi
    fi

    if [[ -n ${ORIGINAL_STTY:-} ]]; then
        stty "$ORIGINAL_STTY" < /dev/tty 2>/dev/null || :
    fi

    engine_cleanup
    if (( TUI_STARTED )) && [[ -t 1 ]]; then
        printf '\n' 2>/dev/null || :
    fi
}

suspend_ui() {
    MOUSE_CLICK_PENDING=0
    printf '%s%s%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" "$C_RESET" "$ALT_SCREEN_OFF"
    stty "$ORIGINAL_STTY" < /dev/tty || exit 1
    TUI_STARTED=0
    kill -s STOP "$$"
    stty -icanon -echo -ixon min 1 time 0 < /dev/tty || exit 1
    TUI_STARTED=1
    printf '%s%s%s%s%s' "$ALT_SCREEN_ON" "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"
    RESIZE_PENDING=1
}

update_terminal_size() {
    local size
    if size=$(stty size < /dev/tty 2>/dev/null); then
        TERM_ROWS=${size%% *}
        TERM_COLS=${size##* }
    else
        TERM_ROWS=0
        TERM_COLS=0
    fi
}

terminal_size_ok() {
    (( TERM_COLS >= MIN_TERM_COLS && TERM_ROWS >= MIN_TERM_ROWS ))
}

draw_small_terminal_notice() {
    printf '%s%s' "$CURSOR_HOME" "$CLR_SCREEN" || true
    printf '%sTerminal too small%s\n' "$C_RED" "$C_RESET" || true
    printf '%sNeed at least:%s %d cols × %d rows\n' "$C_YELLOW" "$C_RESET" "$MIN_TERM_COLS" "$MIN_TERM_ROWS" || true
    printf '%sCurrent size:%s %d cols × %d rows\n' "$C_WHITE" "$C_RESET" "$TERM_COLS" "$TERM_ROWS" || true
    printf '%sResize the terminal, then continue. Press q to quit.%s%s' "$C_CYAN" "$C_RESET" "$CLR_EOS" || true
}

get_active_context() {
    if (( CURRENT_VIEW == 0 )); then
        REPLY_CTX=${CURRENT_TAB}
        REPLY_REF="TAB_ITEMS_${CURRENT_TAB}"
    else
        REPLY_CTX=${CURRENT_MENU_ID}
        REPLY_REF="SUBMENU_ITEMS_${CURRENT_MENU_ID}"
    fi
}

strip_ansi() {
    local v=$1
    v=${v//$'\033'\[*([0-9;:?<=>])@([@A-Z[\\\]^_\`a-z\{\|\}~])/}
    REPLY=$v
}

load_active_values() {
    local REPLY_REF REPLY_CTX item key type block min cache_key value
    get_active_context
    local -n _lav_items_ref="$REPLY_REF"

    for item in "${_lav_items_ref[@]}"; do
        local dummy_max dummy_step
        IFS='|' read -r key type block min dummy_max dummy_step <<< "${ITEM_MAP["${REPLY_CTX}::${item}"]}"
        cache_key="${key}|${block}"
        if [[ -n ${ENGINE_STATE[$cache_key]+_} ]]; then
            value=${ENGINE_STATE[$cache_key]}
            if [[ $type == cycle ]]; then
                cycle_display_value "$value" "$min"
                value=$REPLY
            fi
            VALUE_CACHE["${REPLY_CTX}::${item}"]=$value
        else
            unset 'VALUE_CACHE[${REPLY_CTX}::${item}]'
        fi
    done
}

calc_float() {
    local current=$1 direction=$2 step=$3 min=$4 max=$5
    LC_ALL=C awk -v c="$current" -v dir="$direction" -v step="$step" -v min="$min" -v max="$max" 'BEGIN {
        v = c + dir * step
        if (min != "" && v < min) v = min
        if (max != "" && v > max) v = max
        if (sprintf("%g", v) ~ /inf|nan/) exit 1
        # Fifteen significant digits avoid binary rounding noise without
        # discarding small steps or emitting huge fixed-point strings.
        if (v == 0) v = 0
        printf "%.15g\n", v
    }'
}

modify_value() {
    local label=$1
    local -i direction=$2
    local REPLY_REF REPLY_CTX key type block min max step current new_val
    get_active_context
    local -n _items_ref="$REPLY_REF"
    IFS='|' read -r key type block min max step <<< "${ITEM_MAP["${REPLY_CTX}::${label}"]}"
    local cache_key="${key}|${block}" expected_present=0 expected_value
    [[ ${ENGINE_STATE[$cache_key]+present} ]] && expected_present=1
    expected_value=${ENGINE_STATE[$cache_key]-}
    load_active_values
    current=${VALUE_CACHE["${REPLY_CTX}::${label}"]:-}

    if [[ ! ${VALUE_CACHE["${REPLY_CTX}::${label}"]+present} || -z $current ]]; then
        current=${DEFAULTS["${REPLY_CTX}::${label}"]:-}
        [[ -z $current ]] && current=${min:-0}
    fi

    case $type in
        int)
            [[ $current =~ ^-?[0-9]+$ ]] || current=${min:-0}
            local unsigned int_val int_step min_i max_i
            unsigned=${current#-}
            if (( ${#unsigned} > 18 )); then
                current=${min:-0}
                [[ $current =~ ^-?[0-9]+$ ]] || current=0
                unsigned=${current#-}
            fi
            int_val=$(( 10#${unsigned:-0} ))
            [[ $current == -* ]] && int_val=$(( -int_val ))
            int_step=$(( 10#${step:-1} ))
            if [[ ! $int_step =~ ^[0-9]+$ || ${#int_step} -gt 18 || $int_step == 0 ]]; then int_step=1; fi
            int_val=$(( int_val + direction * int_step ))
            if [[ -n $min ]]; then
                unsigned=${min#-}
                if (( ${#unsigned} <= 18 )); then
                    min_i=$(( 10#${unsigned:-0} )); [[ $min == -* ]] && min_i=$(( -min_i ))
                    if (( int_val < min_i )); then int_val=$min_i; fi
                fi
            fi
            if [[ -n $max ]]; then
                unsigned=${max#-}
                if (( ${#unsigned} <= 18 )); then
                    max_i=$(( 10#${unsigned:-0} )); [[ $max == -* ]] && max_i=$(( -max_i ))
                    if (( int_val > max_i )); then int_val=$max_i; fi
                fi
            fi
            if ! is_int_literal "$int_val"; then
                set_status "Integer adjustment exceeds the supported 18-digit range."
                return 0
            fi
            new_val=$int_val
            ;;
        float)
            [[ $current =~ ^-?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?$ ]] || current=${min:-0.0}
            if ! new_val=$(calc_float "$current" "$direction" "${step:-0.1}" "$min" "$max"); then
                set_status "Float adjustment exceeds the finite numeric range."
                return 0
            fi
            ;;
        bool)
            case ${current,,} in true|yes|on|1) new_val=false ;; *) new_val=true ;; esac
            ;;
        cycle)
            local -a raw_opts=() opts=()
            local -i count idx=0 i
            local opt
            IFS=',' read -r -a raw_opts <<< "$min"
            for opt in "${raw_opts[@]}"; do
                trim_spaces "$opt"
                opts+=("$REPLY")
            done
            count=${#opts[@]}
            if (( count == 0 )); then return 0; fi
            for (( i = 0; i < count; i++ )); do
                if [[ ${opts[i]} == "$current" ]]; then idx=$i; break; fi
            done
            idx=$(( (idx + direction + count) % count ))
            new_val=${opts[idx]}
            ;;
        menu|action|string) return 0 ;;
        *) return 0 ;;
    esac

    if tui_write "$key" "$new_val" "$block" set "$expected_present" "$expected_value" "$type"; then
        load_active_values
        clear_status
        if (( ENGINE_CHANGED )); then post_write_action; fi
    else
        load_active_values
    fi
    return 0
}

reset_current_item() {
    local REPLY_REF REPLY_CTX label type key block def_val
    get_active_context
    local -n _items_ref="$REPLY_REF"
    if (( ${#_items_ref[@]} == 0 )); then return 0; fi
    label=${_items_ref[SELECTED_ROW]}
    local dummy_min dummy_max dummy_step
    # shellcheck disable=SC2034
    IFS='|' read -r key type block dummy_min dummy_max dummy_step <<< "${ITEM_MAP["${REPLY_CTX}::${label}"]:-}"
    
    if [[ $type == action || $type == menu ]]; then return 0; fi
    
    # Grab the explicitly registered default value, if any
    def_val=${DEFAULTS["${REPLY_CTX}::${label}"]:-}
    
    if [[ ${DEFAULTS["${REPLY_CTX}::${label}"]+present} ]]; then
        if tui_write "$key" "$def_val" "$block" set '' '' "$type"; then
            load_active_values
            set_status "Reset '$label' to default ($def_val)."
            if (( ENGINE_CHANGED )); then post_write_action; fi
        else
            load_active_values
            set_status "Failed to reset '$label': $STATUS_MESSAGE"
        fi
    else
        if tui_write "$key" "" "$block" delete '' '' "$type"; then
            load_active_values
            set_status "Reset '$label' to default (UNSET)."
            if (( ENGINE_CHANGED )); then post_write_action; fi
        else
            load_active_values
            set_status "Failed to reset '$label': $STATUS_MESSAGE"
        fi
    fi
    return 0
}

set_absolute_value() {
    local label=$1 new_val=$2 operation=${3:-set}
    local REPLY_REF REPLY_CTX key type block
    get_active_context
    local dummy_min dummy_max dummy_step
    # shellcheck disable=SC2034
    IFS='|' read -r key type block dummy_min dummy_max dummy_step <<< "${ITEM_MAP["${REPLY_CTX}::${label}"]}"
    if tui_write "$key" "$new_val" "$block" "$operation" '' '' "$type"; then
        load_active_values
        return 0
    fi
    return 1
}

reset_defaults() {
    local REPLY_REF REPLY_CTX item def_val type operation
    local -i any_written=0 any_failed=0
    get_active_context
    local -n _rd_items_ref="$REPLY_REF"

    for item in "${_rd_items_ref[@]}"; do
        local dummy_key dummy_block dummy_min dummy_max dummy_step
        # shellcheck disable=SC2034
        IFS='|' read -r dummy_key type dummy_block dummy_min dummy_max dummy_step <<< "${ITEM_MAP["${REPLY_CTX}::${item}"]}"
        case $type in menu|action) continue ;; esac
        def_val=${DEFAULTS["${REPLY_CTX}::${item}"]:-}
        operation="set"
        if [[ ! ${DEFAULTS["${REPLY_CTX}::${item}"]+present} ]]; then operation=delete; fi
        if set_absolute_value "$item" "$def_val" "$operation"; then
            if (( ENGINE_CHANGED )); then any_written=1; fi
        else
            any_failed=1
        fi
    done

    if (( !any_failed )); then clear_status; fi
    if (( any_written )); then post_write_action; fi
    if (( any_failed )); then set_status "Some defaults were not written.${STATUS_MESSAGE:+ $STATUS_MESSAGE}"; fi
    return 0
}

acquire_sudo() {
    if ! command -v sudo >/dev/null 2>&1; then
        set_status "This action requires sudo, which is not installed."
        return 1
    fi
    if sudo -n true 2>/dev/null; then
        return 0
    fi

    printf '%s%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" "$C_RESET" 2>/dev/null || :
    [[ -n ${ORIGINAL_STTY:-} ]] && stty "$ORIGINAL_STTY" < /dev/tty 2>/dev/null || :

    printf '%s%s' "$CLR_SCREEN" "$CURSOR_HOME"
    printf '\n  %s┌──────────────────────────────────────────────────┐%s\n' "$C_MAGENTA" "$C_RESET"
    printf '  %s│%s  System operation requires administrator access  %s│%s\n' "$C_MAGENTA" "$C_YELLOW" "$C_MAGENTA" "$C_RESET"
    printf '  %s└──────────────────────────────────────────────────┘%s\n\n' "$C_MAGENTA" "$C_RESET"

    local -i result=0
    sudo -v 2>/dev/null || result=$?

    stty -icanon -echo -ixon min 1 time 0 < /dev/tty 2>/dev/null || :
    printf '%s%s%s%s' "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"

    if (( result == 0 )); then
        set_status "Authentication successful."
        return 0
    fi
    set_status "Authentication failed or cancelled."
    return 1
}

prompt_line_input() {
    local prompt_text=$1 __result_var=$2 __raw_input="" prompt_row input_ok=0
    [[ $__result_var =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]] || return 1
    printf '%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" || true
    stty "$ORIGINAL_STTY" < /dev/tty 2>/dev/null || :

    prompt_row=$(( HEADER_ROWS + MAX_DISPLAY_ROWS + 7 ))
    if (( prompt_row > TERM_ROWS - 1 )); then prompt_row=$(( TERM_ROWS - 1 )); fi
    printf '\033[%d;1H%s' "$prompt_row" "$CLR_EOS" || true
    printf '%s%s%s ' "$C_YELLOW" "$prompt_text" "$C_RESET" || true

    IFS= read -r -e __raw_input < /dev/tty && input_ok=1

    stty -icanon -echo -ixon min 1 time 0 < /dev/tty 2>/dev/null || :
    printf '%s%s%s%s' "$CURSOR_HIDE" "$MOUSE_ON" "$CLR_SCREEN" "$CURSOR_HOME" || true

    if (( !input_ok )); then set_status "Input cancelled."; return 1; fi
    trim_spaces "$__raw_input"
    printf -v "$__result_var" '%s' "$REPLY"
}

compute_scroll_window() {
    local -i count=$1
    if (( count == 0 )); then
        SELECTED_ROW=0; SCROLL_OFFSET=0; _vis_start=0; _vis_end=0; return 0
    fi
    if (( SELECTED_ROW < 0 )); then SELECTED_ROW=0; fi
    if (( SELECTED_ROW >= count )); then SELECTED_ROW=$(( count - 1 )); fi
    if (( SELECTED_ROW < SCROLL_OFFSET )); then SCROLL_OFFSET=$SELECTED_ROW; fi
    if (( SELECTED_ROW >= SCROLL_OFFSET + MAX_DISPLAY_ROWS )); then SCROLL_OFFSET=$(( SELECTED_ROW - MAX_DISPLAY_ROWS + 1 )); fi
    local -i max_scroll=$(( count - MAX_DISPLAY_ROWS ))
    if (( max_scroll < 0 )); then max_scroll=0; fi
    if (( SCROLL_OFFSET < 0 )); then SCROLL_OFFSET=0; fi
    if (( SCROLL_OFFSET > max_scroll )); then SCROLL_OFFSET=$max_scroll; fi
    _vis_start=$SCROLL_OFFSET
    _vis_end=$(( SCROLL_OFFSET + MAX_DISPLAY_ROWS ))
    if (( _vis_end > count )); then _vis_end=$count; fi
    return 0
}

render_scroll_indicator() {
    local -n _buf=$1
    local position=$2
    local -i count=$3 boundary=$4
    if [[ $position == above ]]; then
        if (( SCROLL_OFFSET > 0 )); then _buf+="${C_GREY}    ▲ (more above)${CLR_EOL}${C_RESET}"$'\n'; else _buf+="${CLR_EOL}"$'\n'; fi
    else
        if (( count > MAX_DISPLAY_ROWS )); then
            local position_info="[$(( SELECTED_ROW + 1 ))/${count}]"
            if (( boundary < count )); then _buf+="${C_GREY}    ▼ (more below) ${position_info}${CLR_EOL}${C_RESET}"$'\n'; else _buf+="${C_GREY}                   ${position_info}${CLR_EOL}${C_RESET}"$'\n'; fi
        else
            _buf+="${CLR_EOL}"$'\n'
        fi
    fi
}

render_item_list() {
    local -n _buf=$1
    local -n _items=$2
    local ctx=$3
    local -i vs=$4 ve=$5 ri
    local item val display type config padded_item max_len def_marker def_val

    for (( ri = vs; ri < ve; ri++ )); do
        item=${_items[ri]}
        val=${VALUE_CACHE["${ctx}::${item}"]-$UNSET_MARKER}
        val=${val//[[:cntrl:]]/?}
        config=${ITEM_MAP["${ctx}::${item}"]}
        local dummy_key dummy_block dummy_min dummy_max dummy_step
        # shellcheck disable=SC2034
        IFS='|' read -r dummy_key type dummy_block dummy_min dummy_max dummy_step <<< "$config"
        
        def_val=${DEFAULTS["${ctx}::${item}"]:-}
        if [[ $type == cycle && ${DEFAULTS["${ctx}::${item}"]+present} ]]; then
            cycle_display_value "$def_val" "$dummy_min"; def_val=$REPLY
        fi
        def_marker="  "
        if [[ ${DEFAULTS["${ctx}::${item}"]+present} ]]; then
            if [[ ${VALUE_CACHE["${ctx}::${item}"]+present} && $val != "$def_val" ]]; then
                def_marker="${C_RED}● ${C_RESET}"
            else
                def_marker="${C_YELLOW}● ${C_RESET}"
            fi
        fi

        case $type in
            menu) display="${C_YELLOW}[+] Open Menu ...${C_RESET}" ;;
            action) display="${C_GREEN}▶ press Enter${C_RESET}" ;;
            string)
                if [[ ! ${VALUE_CACHE["${ctx}::${item}"]+present} ]]; then
                    display="${C_GREEN}[✎ Edit]${C_RESET} ${C_YELLOW}⚠ UNSET${C_RESET}"
                else
                    local -i max_v=$(( BOX_INNER_WIDTH - ITEM_PADDING - 12 ))
                    if (( ${#val} > max_v )); then
                        display="${C_GREEN}[✎]${C_RESET} ${C_WHITE}${val:0:max_v}…${C_RESET}"
                    else
                        display="${C_GREEN}[✎]${C_RESET} ${C_WHITE}${val}${C_RESET}"
                    fi
                fi
                ;;
            *)
                if [[ ! ${VALUE_CACHE["${ctx}::${item}"]+present} ]]; then
                    display="${C_YELLOW}⚠ UNSET${C_RESET}"
                elif [[ $type == bool ]]; then
                    case ${val,,} in
                        true|yes|on|1) display="${C_GREEN}ON${C_RESET}" ;;
                        false|no|off|0) display="${C_RED}OFF${C_RESET}" ;;
                        *) display="${C_YELLOW}${val:0:32}${C_RESET}" ;;
                    esac
                else
                    local -i max_v=$(( BOX_INNER_WIDTH - ITEM_PADDING - 8 ))
                    if (( max_v < 1 )); then max_v=1; fi
                    if (( ${#val} > max_v )); then
                        display="${C_WHITE}${val:0:max_v}…${C_RESET}"
                    else
                        display="${C_WHITE}${val}${C_RESET}"
                    fi
                fi
                ;;
        esac
        max_len=$(( ITEM_PADDING - 1 ))
        if (( ${#item} > ITEM_PADDING )); then
            printf -v padded_item "%-${max_len}ls…" "${item:0:max_len}"
        else
            printf -v padded_item "%-${ITEM_PADDING}ls" "$item"
        fi
        if (( ri == SELECTED_ROW )); then
            _buf+="${C_CYAN} ➤ ${C_INVERSE}${padded_item}${C_RESET} ${def_marker}: ${display}${CLR_EOL}"$'\n'
        else
            _buf+="    ${padded_item} ${def_marker}: ${display}${CLR_EOL}"$'\n'
        fi
    done

    local -i rows_rendered=$(( ve - vs ))
    for (( ri = rows_rendered; ri < MAX_DISPLAY_ROWS; ri++ )); do _buf+="${CLR_EOL}"$'\n'; done
}

render_footer() {
    local -n _footer_buf=$1
    local fallback=$2 text=" Status: $STATUS_MESSAGE"
    if [[ -z $STATUS_MESSAGE ]]; then text=$fallback; fi
    text=${text//[[:cntrl:]]/?}
    if (( ${#text} > TERM_COLS - 1 )); then text="${text:0:TERM_COLS-2}…"; fi
    _footer_buf+="${C_CYAN}${text}${C_RESET}${CLR_EOL}${CLR_EOS}"
}

draw_main_view() {
    local buf="" pad_buf="" tab_line name display_name item_var
    local -i i current_col=3 zone_start count left_pad right_pad vis_len _vis_start _vis_end

    buf+="${CURSOR_HOME}${C_MAGENTA}┌${H_LINE}┐${C_RESET}${CLR_EOL}"$'\n'
    strip_ansi "$APP_TITLE"; local -i t_len=${#REPLY}
    strip_ansi "$APP_VERSION"; local -i v_len=${#REPLY}
    vis_len=$(( t_len + v_len + 1 ))
    
    left_pad=$(( (BOX_INNER_WIDTH - vis_len) / 2 ))
    if (( left_pad < 0 )); then left_pad=0; fi
    right_pad=$(( BOX_INNER_WIDTH - vis_len - left_pad ))
    if (( right_pad < 0 )); then right_pad=0; fi
    
    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad_buf}${C_WHITE}${APP_TITLE} ${C_CYAN}${APP_VERSION}${C_MAGENTA}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}│${C_RESET}${CLR_EOL}"$'\n'

    if (( TAB_SCROLL_START > CURRENT_TAB )); then TAB_SCROLL_START=$CURRENT_TAB; fi
    if (( TAB_SCROLL_START < 0 )); then TAB_SCROLL_START=0; fi
    local -i max_tab_width=$(( BOX_INNER_WIDTH - 6 ))
    local -i total_tab_width=0
    for name in "${TABS[@]}"; do total_tab_width=$(( total_tab_width + ${#name} + 4 )); done
    total_tab_width=$(( total_tab_width - 2 ))
    if (( total_tab_width <= BOX_INNER_WIDTH - 2 )); then
        TAB_SCROLL_START=0
        max_tab_width=$BOX_INNER_WIDTH
    fi
    LEFT_ARROW_ZONE=""; RIGHT_ARROW_ZONE=""

    while true; do
        tab_line="${C_MAGENTA}│ "
        current_col=3
        TAB_ZONES=()
        local -i used_len=0
        
        if (( TAB_SCROLL_START > 0 )); then
            tab_line+="${C_YELLOW}«${C_RESET} "
            LEFT_ARROW_ZONE="2:$(( current_col + 1 ))"
        else
            tab_line+="  "
        fi
        used_len=$(( used_len + 2 )); current_col=$(( current_col + 2 ))

        for (( i = TAB_SCROLL_START; i < TAB_COUNT; i++ )); do
            name=${TABS[i]}; display_name=$name
            local -i tab_name_len=${#name}
            
            # Determine if this is strictly the last tab
            local -i is_last=0
            if (( i == TAB_COUNT - 1 )); then is_last=1; fi
            
            local -i chunk_len=$(( tab_name_len + 2 ))
            if (( ! is_last )); then chunk_len=$(( chunk_len + 2 )); fi
            
            local -i reserve=0
            if (( ! is_last )); then reserve=2; fi
            
            if (( used_len + chunk_len + reserve > max_tab_width )); then
                if (( i < CURRENT_TAB || (i == CURRENT_TAB && TAB_SCROLL_START < CURRENT_TAB) )); then
                    TAB_SCROLL_START=$(( TAB_SCROLL_START + 1 )); continue 2
                fi
                if (( i == CURRENT_TAB )); then
                    local -i avail_label=$(( max_tab_width - used_len - reserve - 2 ))
                    if (( ! is_last )); then avail_label=$(( avail_label - 2 )); fi
                    
                    if (( avail_label < 1 )); then avail_label=1; fi
                    if (( tab_name_len > avail_label )); then
                        if (( avail_label == 1 )); then display_name="…"; else display_name="${name:0:avail_label-1}…"; fi
                        tab_name_len=${#display_name}
                        chunk_len=$(( tab_name_len + 2 ))
                        if (( ! is_last )); then chunk_len=$(( chunk_len + 2 )); fi
                    fi
                    zone_start=$current_col
                    if (( is_last )); then
                        tab_line+="${C_CYAN}${C_INVERSE} ${display_name} ${C_RESET}"
                    else
                        tab_line+="${C_CYAN}${C_INVERSE} ${display_name} ${C_RESET}${C_MAGENTA}│ "
                    fi
                    TAB_ZONES+=("${zone_start}:$(( zone_start + tab_name_len + 1 ))")
                    used_len=$(( used_len + chunk_len )); current_col=$(( current_col + chunk_len ))
                    if (( ! is_last )); then
                        tab_line+="${C_YELLOW}» ${C_RESET}"
                        RIGHT_ARROW_ZONE="$current_col:$(( BOX_INNER_WIDTH + 1 ))"
                        used_len=$(( used_len + 2 ))
                    fi
                    break
                fi
                tab_line+="${C_YELLOW}» ${C_RESET}"
                RIGHT_ARROW_ZONE="$current_col:$(( BOX_INNER_WIDTH + 1 ))"
                used_len=$(( used_len + 2 ))
                break
            fi
            
            zone_start=$current_col
            if (( i == CURRENT_TAB )); then
                if (( is_last )); then
                    tab_line+="${C_CYAN}${C_INVERSE} ${display_name} ${C_RESET}"
                else
                    tab_line+="${C_CYAN}${C_INVERSE} ${display_name} ${C_RESET}${C_MAGENTA}│ "
                fi
            else
                if (( is_last )); then
                    tab_line+="${C_GREY} ${display_name} ${C_RESET}"
                else
                    tab_line+="${C_GREY} ${display_name} ${C_MAGENTA}│ "
                fi
            fi
            TAB_ZONES+=("${zone_start}:$(( zone_start + tab_name_len + 1 ))")
            used_len=$(( used_len + chunk_len )); current_col=$(( current_col + chunk_len ))
        done
        # Center the complete tab group; overflowing groups keep arrow navigation.
        if (( TAB_SCROLL_START == 0 )) && [[ -z $RIGHT_ARROW_ZONE ]]; then
            local -i tab_content_width=$(( used_len - 2 )) tab_shift
            left_pad=$(( (BOX_INNER_WIDTH - tab_content_width) / 2 ))
            tab_shift=$(( left_pad - 3 ))
            local tab_prefix="${C_MAGENTA}│   "
            printf -v pad_buf '%*s' "$left_pad" ''
            tab_line="${C_MAGENTA}│${pad_buf}${tab_line:${#tab_prefix}}"
            for (( i=0; i<${#TAB_ZONES[@]}; i++ )); do
                TAB_ZONES[i]="$(( ${TAB_ZONES[i]%%:*} + tab_shift )):$(( ${TAB_ZONES[i]##*:} + tab_shift ))"
            done
            used_len=$(( left_pad + tab_content_width - 1 ))
        fi
        local -i pad=$(( BOX_INNER_WIDTH - used_len - 1 ))
        if (( pad > 0 )); then printf -v pad_buf '%*s' "$pad" ''; tab_line+="$pad_buf"; fi
        tab_line+="${C_MAGENTA}│${C_RESET}"
        break
    done

    buf+="${tab_line}${CLR_EOL}"$'\n'
    buf+="${C_MAGENTA}└${H_LINE}┘${C_RESET}${CLR_EOL}"$'\n'

    item_var="TAB_ITEMS_${CURRENT_TAB}"
    local -n _draw_items_ref="$item_var"
    count=${#_draw_items_ref[@]}
    compute_scroll_window "$count"
    render_scroll_indicator buf above "$count" "$_vis_start"
    render_item_list buf _draw_items_ref "${CURRENT_TAB}" "$_vis_start" "$_vis_end"
    render_scroll_indicator buf below "$count" "$_vis_end"

    buf+=$'\n'"${C_CYAN} [Tab] Category   [r] Reset Item   [R] Reset All   [←/→ h/l] Adjust${C_RESET}${CLR_EOL}"$'\n'
    buf+="${C_CYAN} [Enter] Action   [F5] Reload   [q] Quit   ${C_YELLOW}●${C_CYAN} Default  ${C_RED}●${C_CYAN} Modified${C_RESET}${CLR_EOL}"$'\n'
    render_footer buf " File: $ENGINE_TARGET"
    printf '%s' "$buf" || true
}

draw_detail_view() {
    local buf="" pad_buf="" items_var breadcrumb title sub
    local -i count pad_needed left_pad right_pad vis_len _vis_start _vis_end
    
    buf+="${CURSOR_HOME}${C_MAGENTA}┌${H_LINE}┐${C_RESET}${CLR_EOL}"$'\n'
    title=" DETAIL VIEW "; sub=" ${CURRENT_MENU_ID} "
    strip_ansi "$title"; local -i t_len=${#REPLY}; strip_ansi "$sub"; local -i s_len=${#REPLY}
    vis_len=$(( t_len + s_len ))
    
    left_pad=$(( (BOX_INNER_WIDTH - vis_len) / 2 ))
    if (( left_pad < 0 )); then left_pad=0; fi
    right_pad=$(( BOX_INNER_WIDTH - vis_len - left_pad ))
    if (( right_pad < 0 )); then right_pad=0; fi
    
    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad_buf}${C_YELLOW}${title}${C_GREY}${sub}${C_MAGENTA}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}│${C_RESET}${CLR_EOL}"$'\n'
    
    breadcrumb=" « Back to ${TABS[CURRENT_TAB]}"
    strip_ansi "$breadcrumb"; local -i b_len=${#REPLY}
    
    pad_needed=$(( BOX_INNER_WIDTH - b_len ))
    if (( pad_needed < 0 )); then pad_needed=0; fi
    
    printf -v pad_buf '%*s' "$pad_needed" ''
    buf+="${C_MAGENTA}│${C_CYAN}${breadcrumb}${C_RESET}${pad_buf}${C_MAGENTA}│${C_RESET}${CLR_EOL}"$'\n'
    buf+="${C_MAGENTA}└${H_LINE}┘${C_RESET}${CLR_EOL}"$'\n'

    items_var="SUBMENU_ITEMS_${CURRENT_MENU_ID}"
    local -n _detail_items_ref="$items_var"
    count=${#_detail_items_ref[@]}
    compute_scroll_window "$count"
    render_scroll_indicator buf above "$count" "$_vis_start"
    render_item_list buf _detail_items_ref "${CURRENT_MENU_ID}" "$_vis_start" "$_vis_end"
    render_scroll_indicator buf below "$count" "$_vis_end"
    
    buf+=$'\n'"${C_CYAN} [Esc/Sh+Tab] Back   [r] Reset Item   [R] Reset All   [←/→ h/l] Adjust${C_RESET}${CLR_EOL}"$'\n'
    buf+="${C_CYAN} [Enter] Action   [F5] Reload   [q] Quit   ${C_YELLOW}●${C_CYAN} Default  ${C_RED}●${C_CYAN} Modified${C_RESET}${CLR_EOL}"$'\n'
    render_footer buf " Submenu: $CURRENT_MENU_ID"
    printf '%s' "$buf" || true
}

draw_picker_view() {
    local buf="" pad_buf="" title sub breadcrumb item hint padded hint_trim
    local -i left_pad right_pad vis_len pad_needed count i vstart vend rows_rendered max_len
    
    buf+="${CURSOR_HOME}${C_MAGENTA}┌${H_LINE}┐${C_RESET}${CLR_EOL}"$'\n'
    title=" PICKER "; sub=" ${PICKER_TITLE} "
    strip_ansi "$title"; local -i t_len=${#REPLY}; strip_ansi "$sub"; local -i s_len=${#REPLY}
    vis_len=$(( t_len + s_len ))
    
    left_pad=$(( (BOX_INNER_WIDTH - vis_len) / 2 ))
    if (( left_pad < 0 )); then left_pad=0; fi
    right_pad=$(( BOX_INNER_WIDTH - vis_len - left_pad ))
    if (( right_pad < 0 )); then right_pad=0; fi
    
    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad_buf}${C_YELLOW}${title}${C_GREY}${sub}${C_MAGENTA}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}│${C_RESET}${CLR_EOL}"$'\n'
    
    breadcrumb=" « Esc to cancel"
    strip_ansi "$breadcrumb"; local -i b_len=${#REPLY}
    
    pad_needed=$(( BOX_INNER_WIDTH - b_len ))
    if (( pad_needed < 0 )); then pad_needed=0; fi
    
    printf -v pad_buf '%*s' "$pad_needed" ''
    buf+="${C_MAGENTA}│${C_CYAN}${breadcrumb}${C_RESET}${pad_buf}${C_MAGENTA}│${C_RESET}${CLR_EOL}"$'\n'
    buf+="${C_MAGENTA}└${H_LINE}┘${C_RESET}${CLR_EOL}"$'\n'

    count=${#PICKER_ITEMS[@]}
    if (( count == 0 )); then
        PICKER_SELECTED=0; PICKER_SCROLL=0
    else
        if (( PICKER_SELECTED < 0 )); then PICKER_SELECTED=0; fi
        if (( PICKER_SELECTED >= count )); then PICKER_SELECTED=$(( count - 1 )); fi
        if (( PICKER_SELECTED < PICKER_SCROLL )); then PICKER_SCROLL=$PICKER_SELECTED; fi
        if (( PICKER_SELECTED >= PICKER_SCROLL + MAX_DISPLAY_ROWS )); then PICKER_SCROLL=$(( PICKER_SELECTED - MAX_DISPLAY_ROWS + 1 )); fi
        local -i max_scroll=$(( count - MAX_DISPLAY_ROWS ))
        if (( max_scroll < 0 )); then max_scroll=0; fi
        if (( PICKER_SCROLL < 0 )); then PICKER_SCROLL=0; fi
        if (( PICKER_SCROLL > max_scroll )); then PICKER_SCROLL=$max_scroll; fi
    fi
    vstart=$PICKER_SCROLL
    vend=$(( PICKER_SCROLL + MAX_DISPLAY_ROWS ))
    if (( vend > count )); then vend=$count; fi
    
    if (( PICKER_SCROLL > 0 )); then buf+="${C_GREY}    ▲ (more above)${CLR_EOL}${C_RESET}"$'\n'; else buf+="${CLR_EOL}"$'\n'; fi
    
    max_len=$(( ITEM_PADDING - 1 ))
    for (( i = vstart; i < vend; i++ )); do
        item=${PICKER_ITEMS[i]}; hint=${PICKER_HINTS[i]:-}
        if (( ${#item} > ITEM_PADDING )); then printf -v padded "%-${max_len}ls…" "${item:0:max_len}"; else printf -v padded "%-${ITEM_PADDING}ls" "$item"; fi
        hint_trim=$hint
        if (( ${#hint_trim} > 32 )); then hint_trim="${hint_trim:0:31}…"; fi
        if (( i == PICKER_SELECTED )); then 
            buf+="${C_CYAN} ➤ ${C_INVERSE}${padded}${C_RESET} ${C_GREY}${hint_trim}${C_RESET}${CLR_EOL}"$'\n'
        else 
            buf+="    ${padded} ${C_GREY}${hint_trim}${C_RESET}${CLR_EOL}"$'\n'
        fi
    done
    rows_rendered=$(( vend - vstart ))
    for (( i = rows_rendered; i < MAX_DISPLAY_ROWS; i++ )); do buf+="${CLR_EOL}"$'\n'; done
    if (( count > MAX_DISPLAY_ROWS )); then
        local pos_info="[$(( PICKER_SELECTED + 1 ))/${count}]"
        if (( vend < count )); then 
            buf+="${C_GREY}    ▼ (more below) ${pos_info}${CLR_EOL}${C_RESET}"$'\n'
        else 
            buf+="${C_GREY}                   ${pos_info}${CLR_EOL}${C_RESET}"$'\n'
        fi
    else
        buf+="${CLR_EOL}"$'\n'
    fi
    
    buf+=$'\n'"${C_CYAN} [↑/↓ j/k] Navigate   [Enter] Select${C_RESET}${CLR_EOL}"$'\n'
    buf+="${C_CYAN} [Esc] Cancel   [q] Quit${C_RESET}${CLR_EOL}"$'\n'
    render_footer buf " ${count} item(s) — Esc to go back"
    printf '%s' "$buf" || true
}

draw_ui() {
    if ! terminal_size_ok; then draw_small_terminal_notice; return; fi
    case $CURRENT_VIEW in
        0) draw_main_view ;;
        1) draw_detail_view ;;
        2) draw_picker_view ;;
    esac
}

exit_picker() {
    CURRENT_VIEW=$PICKER_PARENT_VIEW
    SELECTED_ROW=$PICKER_PARENT_ROW
    SCROLL_OFFSET=$PICKER_PARENT_SCROLL
    PICKER_ITEMS=(); PICKER_HINTS=(); PICKER_TITLE=""; PICKER_CALLBACK=""
    load_active_values
}

picker_navigate() {
    local -i dir=$1 count=${#PICKER_ITEMS[@]}
    if (( count == 0 )); then PICKER_SELECTED=0; return 0; fi
    PICKER_SELECTED=$(( ((PICKER_SELECTED + dir) % count + count) % count ))
}

picker_confirm() {
    local -i count=${#PICKER_ITEMS[@]}
    if (( count == 0 )); then exit_picker; return; fi
    local chosen=${PICKER_ITEMS[PICKER_SELECTED]} cb=$PICKER_CALLBACK
    exit_picker
    if [[ -n $cb ]] && declare -F "$cb" >/dev/null; then "$cb" "$chosen"; fi
    return 0
}

navigate() {
    local -i dir=$1 count
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _nav_items_ref="$REPLY_REF"
    count=${#_nav_items_ref[@]}
    if (( count == 0 )); then return 0; fi
    SELECTED_ROW=$(( (SELECTED_ROW + dir + count) % count ))
    clear_status
}

navigate_page() {
    local -i dir=$1 count
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _items_ref="$REPLY_REF"
    count=${#_items_ref[@]}
    if (( count == 0 )); then return 0; fi
    SELECTED_ROW=$(( SELECTED_ROW + dir * MAX_DISPLAY_ROWS ))
    if (( SELECTED_ROW < 0 )); then SELECTED_ROW=0; fi
    if (( SELECTED_ROW >= count )); then SELECTED_ROW=$(( count - 1 )); fi
    clear_status
}

navigate_end() {
    local -i target=$1 count
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _items_ref="$REPLY_REF"
    count=${#_items_ref[@]}
    if (( count == 0 )); then return 0; fi
    if (( target == 0 )); then SELECTED_ROW=0; else SELECTED_ROW=$(( count - 1 )); fi
    clear_status
}

adjust() {
    local -i dir=$1
    local REPLY_REF REPLY_CTX label type
    get_active_context
    local -n _items_ref="$REPLY_REF"
    if (( ${#_items_ref[@]} == 0 )); then return 0; fi
    label=${_items_ref[SELECTED_ROW]}
    local dummy_key dummy_block dummy_min dummy_max dummy_step
    # shellcheck disable=SC2034
    IFS='|' read -r dummy_key type dummy_block dummy_min dummy_max dummy_step <<< "${ITEM_MAP["${REPLY_CTX}::${label}"]}"
    if [[ $type == action || $type == string ]]; then return 0; fi
    modify_value "$label" "$dir"
}

switch_tab() {
    local -i dir=${1:-1}
    TAB_SAVED_ROW[CURRENT_TAB]=$SELECTED_ROW
    TAB_SAVED_SCROLL[CURRENT_TAB]=$SCROLL_OFFSET
    CURRENT_TAB=$(( (CURRENT_TAB + dir + TAB_COUNT) % TAB_COUNT ))
    SELECTED_ROW=${TAB_SAVED_ROW[CURRENT_TAB]:-0}
    SCROLL_OFFSET=${TAB_SAVED_SCROLL[CURRENT_TAB]:-0}
    load_active_values
    clear_status
}

set_tab() {
    local -i idx=$1
    if (( idx != CURRENT_TAB && idx >= 0 && idx < TAB_COUNT )); then
        TAB_SAVED_ROW[CURRENT_TAB]=$SELECTED_ROW
        TAB_SAVED_SCROLL[CURRENT_TAB]=$SCROLL_OFFSET
        CURRENT_TAB=$idx
        SELECTED_ROW=${TAB_SAVED_ROW[CURRENT_TAB]:-0}
        SCROLL_OFFSET=${TAB_SAVED_SCROLL[CURRENT_TAB]:-0}
        load_active_values
        clear_status
    fi
}

activate_item() {
    local REPLY_REF REPLY_CTX item config key type block
    get_active_context
    local -n _act_ref="$REPLY_REF"
    if (( ${#_act_ref[@]} == 0 )); then return 1; fi
    item=${_act_ref[SELECTED_ROW]}
    config=${ITEM_MAP["${REPLY_CTX}::${item}"]}
    local dummy_min dummy_max dummy_step
    # shellcheck disable=SC2034
    IFS='|' read -r key type block dummy_min dummy_max dummy_step <<< "$config"
    case $type in
        menu)
            PARENT_ROW=$SELECTED_ROW; PARENT_SCROLL=$SCROLL_OFFSET
            CURRENT_MENU_ID=$key; CURRENT_VIEW=1; SELECTED_ROW=0; SCROLL_OFFSET=0
            load_active_values
            return 0
            ;;
        action)
            if declare -F "action_${key}" >/dev/null; then
                "action_${key}"
                load_active_values
            else
                set_status "No handler defined for action: $key"
            fi
            return 0
            ;;
        string)
            local user_input="" current_val p_text
            current_val=${VALUE_CACHE["${REPLY_CTX}::${item}"]:-}
            
            p_text="New $item"
            if [[ -n $current_val ]]; then
                p_text+=" (Current: ${current_val:0:15})"
            fi
            p_text+=" (blank to UNSET):"
            
            prompt_line_input "$p_text" user_input || return 0
            if [[ -z $user_input ]]; then
                tui_write "$key" "" "$block" delete '' '' "$type"
            else
                tui_write "$key" "$user_input" "$block" set '' '' "$type"
            fi
            
            load_active_values
            if (( ENGINE_CHANGED )); then post_write_action; fi
            return 0
            ;;
    esac
    return 1
}

go_back() {
    CURRENT_VIEW=0
    SELECTED_ROW=$PARENT_ROW
    SCROLL_OFFSET=$PARENT_SCROLL
    load_active_values
    clear_status
}

classify_mouse_event() {
    local code=$1 x=$2 y=$3 terminator=$4
    local context="${CURRENT_VIEW}:${CURRENT_TAB}:${CURRENT_MENU_ID}"
    REPLY=$code
    if [[ $terminator == m ]]; then
        local pending=$MOUSE_CLICK_PENDING
        MOUSE_CLICK_PENDING=0
        if (( code == 0 && pending && x == MOUSE_PRESS_X && y == MOUSE_PRESS_Y )) &&
           [[ $context == "$MOUSE_PRESS_CONTEXT" ]]; then
            REPLY=0
            return 0
        fi
        return 1
    fi
    case $code in
        0)
            MOUSE_CLICK_PENDING=1; MOUSE_PRESS_X=$x; MOUSE_PRESS_Y=$y
            MOUSE_PRESS_CONTEXT=$context
            REPLY=32
            ;;
        32) MOUSE_CLICK_PENDING=0 ;;
        2|64|65) MOUSE_CLICK_PENDING=0 ;;
        *) MOUSE_CLICK_PENDING=0; return 1 ;;
    esac
    return 0
}

handle_mouse() {
    local input="$1"
    local -i button x y i start end
    local zone

    local body="${input#'[<'}"
    if [[ "$body" == "$input" ]]; then return 0; fi

    local terminator="${body: -1}"
    if [[ "$terminator" != "M" && "$terminator" != "m" ]]; then return 0; fi

    body="${body%[Mm]}"
    local field1 field2 field3
    IFS=';' read -r field1 field2 field3 <<< "$body"
    if [[ ! "$field1" =~ ^[0-9]+$ ]]; then return 0; fi
    if [[ ! "$field2" =~ ^[0-9]+$ ]]; then return 0; fi
    if [[ ! "$field3" =~ ^[0-9]+$ ]]; then return 0; fi

    if (( ${#field1} > 3 || ${#field2} > 6 || ${#field3} > 6 )); then return 0; fi
    button=$((10#$field1)); x=$((10#$field2)); y=$((10#$field3))

    if (( x < 1 || x > MIN_TERM_COLS || y < 1 || y > TERM_ROWS )); then
        MOUSE_CLICK_PENDING=0; return 0
    fi
    classify_mouse_event "$button" "$x" "$y" "$terminator" || return 0
    button=$REPLY
    if (( button == 64 )); then
        if (( y == TAB_ROW && CURRENT_VIEW == 0 )); then switch_tab -1; else navigate -1; fi
        return 0
    fi
    if (( button == 65 )); then
        if (( y == TAB_ROW && CURRENT_VIEW == 0 )); then switch_tab 1; else navigate 1; fi
        return 0
    fi
    if (( button != 0 && button != 2 && button != 32 )); then return 0; fi

    if (( y == TAB_ROW )); then
        if (( CURRENT_VIEW == 0 )); then
            if [[ -n "$LEFT_ARROW_ZONE" ]]; then
                start="${LEFT_ARROW_ZONE%%:*}"
                end="${LEFT_ARROW_ZONE##*:}"
                if [[ -n $start && -n $end ]] && (( x >= start && x <= end )); then
                    switch_tab -1
                    return 0
                fi
            fi

            if [[ -n "$RIGHT_ARROW_ZONE" ]]; then
                start="${RIGHT_ARROW_ZONE%%:*}"
                end="${RIGHT_ARROW_ZONE##*:}"
                if [[ -n $start && -n $end ]] && (( x >= start && x <= end )); then
                    switch_tab 1
                    return 0
                fi
            fi

            for (( i = 0; i < ${#TAB_ZONES[@]}; i++ )); do
                if [[ -z "${TAB_ZONES[i]:-}" ]]; then continue; fi
                zone="${TAB_ZONES[i]}"
                start="${zone%%:*}"
                end="${zone##*:}"
                if [[ -n $start && -n $end ]] && (( x >= start && x <= end )); then
                    set_tab "$(( i + TAB_SCROLL_START ))"
                    return 0
                fi
            done
        else
            if (( button == 0 )); then
                go_back
            fi
            return 0
        fi
    fi

    local -i effective_start=$(( ITEM_START_ROW + 1 ))
    if (( y >= effective_start && y < effective_start + MAX_DISPLAY_ROWS )); then
        local -i clicked_idx=$(( y - effective_start + SCROLL_OFFSET ))

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
            # Motion shares click hit-testing, but can never reach an action.
            if (( button == 32 )); then return 0; fi
            if (( x > ADJUST_THRESHOLD )); then
                if (( button == 0 )); then
                    activate_item || adjust 1
                elif (( button == 2 )); then
                    adjust -1
                fi
            fi
        fi
    fi
    return 0
}

handle_mouse_picker() {
    local input="$1"
    local -i button x y

    local body="${input#'[<'}"
    if [[ "$body" == "$input" ]]; then return 0; fi

    local terminator="${body: -1}"
    if [[ "$terminator" != "M" && "$terminator" != "m" ]]; then return 0; fi
    body="${body%[Mm]}"

    local field1 field2 field3
    IFS=';' read -r field1 field2 field3 <<< "$body"
    if [[ ! "$field1" =~ ^[0-9]+$ ]]; then return 0; fi
    if [[ ! "$field2" =~ ^[0-9]+$ ]]; then return 0; fi
    if [[ ! "$field3" =~ ^[0-9]+$ ]]; then return 0; fi
    if (( ${#field1} > 3 || ${#field2} > 6 || ${#field3} > 6 )); then return 0; fi
    button=$((10#$field1)); x=$((10#$field2)); y=$((10#$field3))

    if (( x < 1 || x > MIN_TERM_COLS || y < 1 || y > TERM_ROWS )); then
        MOUSE_CLICK_PENDING=0; return 0
    fi
    classify_mouse_event "$button" "$x" "$y" "$terminator" || return 0
    button=$REPLY
    if (( button == 64 )); then picker_navigate -1; return 0; fi
    if (( button == 65 )); then picker_navigate 1; return 0; fi
    if (( button != 0 && button != 2 && button != 32 )); then return 0; fi

    local -i effective_start=$(( ITEM_START_ROW + 1 ))
    if (( y >= effective_start && y < effective_start + MAX_DISPLAY_ROWS )); then
        local -i clicked_idx=$(( y - effective_start + PICKER_SCROLL ))
        local -i count=${#PICKER_ITEMS[@]}
        if (( clicked_idx >= 0 && clicked_idx < count )); then
            PICKER_SELECTED=$clicked_idx
            if (( button == 32 )); then return 0; fi
            if (( button == 0 )); then
                picker_confirm
            fi
        fi
    fi
    return 0
}

read_escape_seq() {
    local -n _esc_out=$1
    _esc_out=""
    local char
    if ! IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char < /dev/tty; then return 1; fi
    _esc_out+=$char
    if [[ $char == '[' || $char == 'O' ]]; then
        while (( ${#_esc_out} < MAX_ESCAPE_BYTES )) && IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char < /dev/tty; do
            _esc_out+=$char
            [[ $char == [@-~] ]] && break
        done
    fi
    return 0
}

handle_key_main() {
    local key=$1
    case $key in
        '[Z') switch_tab -1; return ;;
        '[A'|'OA') navigate -1; return ;;
        '[B'|'OB') navigate 1; return ;;
        '[C'|'OC') adjust 1; return ;;
        '[D'|'OD') adjust -1; return ;;
        '[5~') navigate_page -1; return ;;
        '[6~') navigate_page 1; return ;;
        '[H'|'[1~') navigate_end 0; return ;;
        '[F'|'[4~') navigate_end 1; return ;;
        '['*'<'*[Mm]) handle_mouse "$key"; return ;;
    esac
    case $key in
        k|K) navigate -1 ;;
        j|J) navigate 1 ;;
        l|L) adjust 1 ;;
        h|H) adjust -1 ;;
        $'\x15') navigate_page -1 ;; # Ctrl+U
        $'\x04') navigate_page 1 ;;  # Ctrl+D
        g) navigate_end 0 ;;
        G) navigate_end 1 ;;
        $'\t') switch_tab 1 ;;
        r) reset_current_item ;;
        R) reset_defaults ;;
        ''|$'\n') activate_item || adjust 1 ;;
        $'\x7f'|$'\x08'|$'\e\n') adjust -1 ;;
        q|Q|$'\x03') exit 0 ;;
    esac
}

handle_key_detail() {
    local key=$1
    case $key in
        '[A'|'OA') navigate -1; return ;;
        '[B'|'OB') navigate 1; return ;;
        '[C'|'OC') adjust 1; return ;;
        '[D'|'OD') adjust -1; return ;;
        '[5~') navigate_page -1; return ;;
        '[6~') navigate_page 1; return ;;
        '[H'|'[1~') navigate_end 0; return ;;
        '[F'|'[4~') navigate_end 1; return ;;
        '[Z') go_back; return ;;
        '['*'<'*[Mm]) handle_mouse "$key"; return ;;
    esac
    case $key in
        ESC) go_back ;;
        k|K) navigate -1 ;;
        j|J) navigate 1 ;;
        l|L) adjust 1 ;;
        h|H) adjust -1 ;;
        $'\x15') navigate_page -1 ;; # Ctrl+U
        $'\x04') navigate_page 1 ;;  # Ctrl+D
        g) navigate_end 0 ;;
        G) navigate_end 1 ;;
        r) reset_current_item ;;
        R) reset_defaults ;;
        ''|$'\n') activate_item || adjust 1 ;;
        $'\x7f'|$'\x08'|$'\e\n') adjust -1 ;;
        q|Q|$'\x03') exit 0 ;;
    esac
}

handle_key_picker() {
    local key=$1
    case $key in
        '[A'|'OA') picker_navigate -1; return ;;
        '[B'|'OB') picker_navigate 1; return ;;
        '[5~') picker_navigate "-$MAX_DISPLAY_ROWS"; return ;;
        '[6~') picker_navigate "$MAX_DISPLAY_ROWS"; return ;;
        '[H'|'[1~') PICKER_SELECTED=0; return ;;
        '[F'|'[4~') PICKER_SELECTED=$(( ${#PICKER_ITEMS[@]} - 1 )); return ;;
        '['*'<'*[Mm]) handle_mouse_picker "$key"; return ;;
    esac
    case $key in
        ESC) exit_picker ;;
        k|K) picker_navigate -1 ;;
        j|J) picker_navigate 1 ;;
        $'\x15') picker_navigate "-$MAX_DISPLAY_ROWS" ;; # Ctrl+U
        $'\x04') picker_navigate "$MAX_DISPLAY_ROWS" ;;  # Ctrl+D
        g) PICKER_SELECTED=0 ;;
        G) PICKER_SELECTED=$(( ${#PICKER_ITEMS[@]} - 1 )) ;;
        ''|$'\n') picker_confirm ;;
        q|Q|$'\x03') exit 0 ;;
    esac
}

consume_paste_byte() {
    PASTE_TAIL="${PASTE_TAIL}${1}"
    if (( ${#PASTE_TAIL} > 6 )); then PASTE_TAIL=${PASTE_TAIL: -6}; fi
    if [[ $PASTE_TAIL == $'\e[201~' ]]; then PASTE_ACTIVE=0; PASTE_TAIL=""; fi
    return 0
}

discard_bracketed_paste() {
    local char
    PASTE_ACTIVE=1; PASTE_TAIL=""
    # Retain state across timeouts: a slow paste must never become shortcuts.
    while (( PASTE_ACTIVE )) && IFS= read -rsn1 -t "$READ_LOOP_TIMEOUT" char < /dev/tty; do
        consume_paste_byte "$char"
    done
    return 0
}

handle_input_router() {
    local key=$1 escape_seq=""
    if (( PASTE_ACTIVE )); then consume_paste_byte "$key"; return 0; fi
    if [[ $key == $'\x1b' ]]; then
        if read_escape_seq escape_seq; then
            key=$escape_seq
            if [[ $key == "" || $key == $'\n' ]]; then key=$'\e\n'; fi
        else
            key=ESC
        fi
    fi
    if [[ $key == '[200~' ]]; then discard_bracketed_paste; return 0; fi
    if ! terminal_size_ok; then
        MOUSE_CLICK_PENDING=0
        case $key in q|Q|$'\x03') exit 0 ;; esac
        return 0
    fi
    if [[ $key == '[15~' ]]; then
        # Reload on demand without polling or disturbing navigation state.
        # Keep displayed values if the read fails; the next save revalidates.
        if tui_load; then
            if (( CURRENT_VIEW != 2 )); then load_active_values; fi
            set_status "Configuration refreshed."
        fi
        return 0
    fi
    case $CURRENT_VIEW in
        0) handle_key_main "$key" ;;
        1) handle_key_detail "$key" ;;
        2) handle_key_picker "$key" ;;
    esac
}

# Frontend adapters consume the engine result fields without subshells.
tui_load() {
    if engine_load; then return 0; fi
    set_status "${ENGINE_MESSAGE:-Unable to load configuration.}"
    return 1
}

tui_write() {
    ENGINE_CHANGED=0
    if engine_write "$@"; then return 0; fi
    ENGINE_CHANGED=0
    set_status "${ENGINE_MESSAGE:-Unable to save configuration.}"
    return 1
}

# Applications may override this after sourcing the frontend.
post_write_action() { :; }

parse_args() {
    TUI_CHECK_ONLY=0
    while (($#)); do
        case $1 in
            --config)
                shift
                if [[ $# -gt 0 && -n $1 ]]; then CONFIG_FILE=$1
                else log_err "--config requires a path"; return 2; fi
                ;;
            --config=*)
                CONFIG_FILE=${1#--config=}
                [[ -n $CONFIG_FILE ]] || { log_err "--config requires a path"; return 2; }
                ;;
            --check) TUI_CHECK_ONLY=1 ;;
            --help|-h)
                printf 'Usage: %s [--config PATH] [--check]\n' "${0##*/}"
                printf '  --check  Validate the schema and engine interface without opening a config or TTY.\n'
                return 10
                ;;
            *) log_err "Unknown argument: $1"; return 2 ;;
        esac
        shift
    done
}

tui_main() {
    # Launchers source frontend, engine and schema at top level, then call once.
    local arg_status=0 dep callback
    tui_init
    parse_args "$@" || arg_status=$?
    if (( arg_status == 10 )); then return 0; fi
    if (( arg_status )); then return "$arg_status"; fi
    if (( TAB_COUNT == 0 || MAX_DISPLAY_ROWS < 1 )); then
        log_err "Configure at least one tab and one display row."; return 1
    fi
    for callback in register_items engine_init engine_load engine_write engine_cleanup; do
        if ! declare -F "$callback" >/dev/null; then
            log_err "Missing schema/engine function: $callback"; return 1
        fi
    done
    if [[ $(declare -p ENGINE_DEPENDENCIES 2>/dev/null) != 'declare -'*a*' ENGINE_DEPENDENCIES='* ]]; then
        log_err "Engine must declare ENGINE_DEPENDENCIES as an indexed array."; return 1
    fi
    for dep in stty awk "${ENGINE_DEPENDENCIES[@]}"; do
        if ! command -v "$dep" >/dev/null 2>&1; then log_err "Missing dependency: $dep"; return 1; fi
    done
    register_items
    if (( TUI_CHECK_ONLY )); then
        printf 'Schema and engine interface OK (%d tabs).\n' "$TAB_COUNT"
        return 0
    fi
    if [[ ! -t 0 || ! -t 1 ]]; then log_err "Interactive TTY stdin/stdout required"; return 1; fi
    trap cleanup EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 131' QUIT
    trap 'exit 143' TERM
    if ! engine_init "${CONFIG_FILE:-}"; then
        log_err "${ENGINE_MESSAGE:-Engine initialization failed.}"; return 1
    fi
    tui_load || { log_err "$STATUS_MESSAGE"; return 1; }

    ORIGINAL_STTY=$(stty -g < /dev/tty 2>/dev/null) || ORIGINAL_STTY=""
    if [[ -z $ORIGINAL_STTY ]]; then log_err "A controlling TTY is required."; return 1; fi
    if ! stty -icanon -echo -ixon min 1 time 0 < /dev/tty 2>/dev/null; then
        log_err "Failed to configure terminal raw input."; return 1
    fi
    TUI_STARTED=1
    printf '%s%s%s%s%s' "$ALT_SCREEN_ON" "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"

    # Expected callback failures are handled explicitly, as in the original TUI.
    set +e
    load_active_values
    trap 'RESIZE_PENDING=1' WINCH CONT
    trap suspend_ui TSTP
    local key read_status
    local -i redraw=1
    update_terminal_size
    while true; do
        if (( RESIZE_PENDING )); then
            RESIZE_PENDING=0; MOUSE_CLICK_PENDING=0; update_terminal_size; redraw=1
        fi
        if (( redraw )); then draw_ui; redraw=0; fi
        if IFS= read -rsn1 -t "$READ_LOOP_TIMEOUT" key < /dev/tty; then
            if (( RESIZE_PENDING )); then
                RESIZE_PENDING=0; MOUSE_CLICK_PENDING=0; update_terminal_size
            fi
            handle_input_router "$key"
            redraw=1
        else
            read_status=$?
            if (( read_status == 1 )); then return 0; fi
        fi
    done
}
