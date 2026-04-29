#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Dusky TUI Engine - asusctl v6.3.4 Edition (Aura Supported)
# -----------------------------------------------------------------------------
# Target: Arch Linux / Hyprland / asusd
# Purpose: Deep hardware control mapping for ASUS TUF (FX507ZE)
# -----------------------------------------------------------------------------

set -euo pipefail
shopt -s extglob

# =============================================================================
# ▼ USER CONFIGURATION ▼
# =============================================================================

declare -r APP_TITLE="ASUS Hardware Controller"
declare -r APP_VERSION="v1.1.0 (asusctl 6.3.4)"

# Dimensions & Layout
declare -ri MAX_DISPLAY_ROWS=14
declare -ri BOX_INNER_WIDTH=76
declare -ri ADJUST_THRESHOLD=38
declare -ri ITEM_PADDING=32

declare -ri HEADER_ROWS=4
declare -ri TAB_ROW=3
declare -ri ITEM_START_ROW=$(( HEADER_ROWS + 1 ))

declare -ra TABS=("Power" "Lighting" "Armoury")

# Item Registration
# Format: 'key|type|block|min|max|step'
register_items() {
    # Tab 0: Power & Battery
    register 0 "Active Profile"    'active|cycle|profile_active|Quiet,Balanced,Performance||' "Balanced"
    register 0 "AC Profile Bind"   'ac|cycle|profile_ac|Quiet,Balanced,Performance||'         "Performance"
    register 0 "Battery Prof Bind" 'bat|cycle|profile_bat|Quiet,Balanced,Performance||'       "Balanced"
    register 0 "Charge Limit (%)"  'limit|int|battery_limit|20|100|5'                         "80"
    
    # Tab 1: Lighting (Aura & LEDs)
    register 1 "Keyboard Brightness" 'brightness|cycle|leds|Off,Low,Med,High||'               "Med"
    register 1 "Aura Effect Mode"    'effect|cycle|aura|static,breathe,rainbow-cycle,rainbow-wave,pulse||' "static"
    register 1 "Primary Color"       'color|cycle|aura|Red,Green,Blue,White,Yellow,Cyan,Magenta||' "White"
    register 1 "Effect Speed"        'speed|cycle|aura|low,med,high||'                        "med"
    register 1 "Keyboard Power States" 'kbd_power|menu||||' ""

    # Tab 1 Submenu: Power States
    register_child "kbd_power" "Awake"    'awake|bool|aura_power|||' "true"
    register_child "kbd_power" "Boot"     'boot|bool|aura_power|||' "true"
    register_child "kbd_power" "Sleep"    'sleep|bool|aura_power|||' "false"
    register_child "kbd_power" "Shutdown" 'shutdown|bool|aura_power|||' "false"

    # Tab 2: Armoury (Advanced Tuning)
    register 2 "GPU MUX Mode"        'gpu_mux_mode|cycle|armoury|0,1||'                       "0"
    register 2 "dGPU Disable"        'dgpu_disable|cycle|armoury|0,1||'                       "0"
    register 2 "Panel Overdrive"     'panel_overdrive|cycle|armoury|0,1||'                    "0"
    register 2 "CPU Sustained (PL1)" 'ppt_pl1_spl|int|armoury|28|90|1'                        "90"
    register 2 "CPU Burst (PL2)"     'ppt_pl2_sppt|int|armoury|28|135|1'                      "135"
    register 2 "NV Dynamic Boost"    'nv_dynamic_boost|int|armoury|5|15|5'                    "15"
}

# Post-Write Hook
post_write_action() {
    : # Refresh or restart daemons if necessary
}

# =============================================================================
# ▲ END OF USER CONFIGURATION ▲
# =============================================================================

# --- Pre-computed Constants ---
declare _h_line_buf
printf -v _h_line_buf '%*s' "$BOX_INNER_WIDTH" ''
declare -r H_LINE="${_h_line_buf// /─}"
unset _h_line_buf

# --- ANSI Constants ---
declare -r C_RESET=$'\033[0m'
declare -r C_CYAN=$'\033[1;36m'
declare -r C_GREEN=$'\033[1;32m'
declare -r C_MAGENTA=$'\033[1;35m'
declare -r C_RED=$'\033[1;31m'
declare -r C_YELLOW=$'\033[1;33m'
declare -r C_WHITE=$'\033[1;37m'
declare -r C_GREY=$'\033[1;30m'
declare -r C_INVERSE=$'\033[7m'
declare -r CLR_EOL=$'\033[K'
declare -r CLR_EOS=$'\033[J'
declare -r CLR_SCREEN=$'\033[2J'
declare -r CURSOR_HOME=$'\033[H'
declare -r CURSOR_HIDE=$'\033[?25l'
declare -r CURSOR_SHOW=$'\033[?25h'
declare -r MOUSE_ON=$'\033[?1000h\033[?1002h\033[?1006h'
declare -r MOUSE_OFF=$'\033[?1000l\033[?1002l\033[?1006l'

declare -r ESC_READ_TIMEOUT=0.10
declare -r UNSET_MARKER='«unset»'

# --- State Management ---
declare -i SELECTED_ROW=0
declare -i CURRENT_TAB=0
declare -i SCROLL_OFFSET=0
declare -ri TAB_COUNT=${#TABS[@]}
declare -a TAB_ZONES=()
declare -i TAB_SCROLL_START=0
declare ORIGINAL_STTY=""

# View State
declare -i CURRENT_VIEW=0
declare CURRENT_MENU_ID=""
declare -i PARENT_ROW=0
declare -i PARENT_SCROLL=0

# Terminal geometry
declare -i TERM_ROWS=0
declare -i TERM_COLS=0
declare -ri MIN_TERM_COLS=$(( BOX_INNER_WIDTH + 2 ))
declare -ri MIN_TERM_ROWS=$(( HEADER_ROWS + MAX_DISPLAY_ROWS + 5 ))

# Write state
declare -gi LAST_WRITE_CHANGED=0
declare STATUS_MESSAGE=""

# --- Click Zones for Arrows ---
declare LEFT_ARROW_ZONE=""
declare RIGHT_ARROW_ZONE=""

# --- Data Structures ---
declare -A ITEM_MAP=()
declare -A VALUE_CACHE=()
declare -A CONFIG_CACHE=()
declare -A DEFAULTS=()

for (( _ti = 0; _ti < TAB_COUNT; _ti++ )); do
    declare -ga "TAB_ITEMS_${_ti}=()"
done
unset _ti

# --- System Helpers ---

log_err() {
    printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$1" >&2
}

set_status() {
    declare -g STATUS_MESSAGE="$1"
}

clear_status() {
    declare -g STATUS_MESSAGE=""
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
    printf '%s%s' "$CURSOR_HOME" "$CLR_SCREEN"
    printf '%sTerminal too small%s\n' "$C_RED" "$C_RESET"
    printf '%sNeed at least:%s %d cols × %d rows\n' "$C_YELLOW" "$C_RESET" "$MIN_TERM_COLS" "$MIN_TERM_ROWS"
    printf '%sCurrent size:%s %d cols × %d rows\n' "$C_WHITE" "$C_RESET" "$TERM_COLS" "$TERM_ROWS"
    printf '%sResize the terminal, then continue. Press q to quit.%s%s' "$C_CYAN" "$C_RESET" "$CLR_EOS"
}

strip_ansi() {
    local v="$1"
    v="${v//$'\033'\[*([0-9;:?<=>])@([@A-Z\[\\\]^_\`a-z\{|\}~])/}"
    REPLY="$v"
}

# --- Core Logic Engine ---

register() {
    local -i tab_idx=$1
    local label="$2" config="$3" default_val="${4:-}"
    local key type block min max step
    IFS='|' read -r key type block min max step <<< "$config"

    if (( tab_idx < 0 || tab_idx >= TAB_COUNT )); then
        log_err "Register Error: Tab index out of range."
        exit 1
    fi

    ITEM_MAP["${tab_idx}::${label}"]="$config"
    if [[ -n "$default_val" ]]; then
        DEFAULTS["${tab_idx}::${label}"]="$default_val"
    fi

    local -n _reg_tab_ref="TAB_ITEMS_${tab_idx}"
    _reg_tab_ref+=("$label")
}

register_child() {
    local parent_id="$1" label="$2" config="$3" default_val="${4:-}"
    ITEM_MAP["${parent_id}::${label}"]="$config"
    if [[ -n "$default_val" ]]; then
        DEFAULTS["${parent_id}::${label}"]="$default_val"
    fi
    local -n _child_ref="SUBMENU_ITEMS_${parent_id}"
    _child_ref+=("$label")
}

# --- ASUSCTL Interfacing ---

populate_config_cache() {
    CONFIG_CACHE=()

    # Pre-seed Aura/Power defaults as they are write-only or lack easy getter commands
    CONFIG_CACHE["effect|aura"]="static"
    CONFIG_CACHE["color|aura"]="White"
    CONFIG_CACHE["speed|aura"]="med"
    CONFIG_CACHE["awake|aura_power"]="true"
    CONFIG_CACHE["boot|aura_power"]="true"
    CONFIG_CACHE["sleep|aura_power"]="false"
    CONFIG_CACHE["shutdown|aura_power"]="false"

    # Profiles Extraction
    local prof_out
    if prof_out=$(asusctl profile get 2>/dev/null); then
        CONFIG_CACHE["active|profile_active"]=$(echo "$prof_out" | grep "Active profile:" | awk '{print $3}')
        CONFIG_CACHE["ac|profile_ac"]=$(echo "$prof_out" | grep "AC profile" | awk '{print $3}')
        CONFIG_CACHE["bat|profile_bat"]=$(echo "$prof_out" | grep "Battery profile" | awk '{print $3}')
    fi

    # Battery State
    local bat_out
    if bat_out=$(asusctl battery info 2>/dev/null); then
        CONFIG_CACHE["limit|battery_limit"]=$(echo "$bat_out" | grep -o '[0-9]\+')
    fi

    # LED Brightness
    local led_out
    if led_out=$(asusctl leds get 2>/dev/null); then
        local led_raw
        led_raw=$(echo "$led_out" | awk '{print $NF}')
        CONFIG_CACHE["brightness|leds"]="${led_raw^}"
    fi

    # Armoury Attributes 
    # Use 'list' to extract all values in a single DBus call, avoiding minutes of startup hang
    local arm_out
    if arm_out=$(asusctl armoury list 2>/dev/null); then
        eval "$(echo "$arm_out" | awk '
            /^[A-Za-z0-9_]+:/ { 
                key = $1; 
                sub(/:/, "", key); 
            }
            /current:/ && key != "" {
                s = $0;
                val = "";
                if (match(s, /\([0-9]+\)/)) { val = substr(s, RSTART+1, RLENGTH-2) }
                else if (match(s, /\[[0-9]+\]/)) { val = substr(s, RSTART+1, RLENGTH-2) }
                
                if (val != "") {
                    printf "CONFIG_CACHE[\"%s|armoury\"]=\"%s\";\n", key, val
                }
                key = "";
            }
        ')"
    fi
}

write_to_asusctl() {
    local key="$1" new_val="$2" block="${3:-}"
    local cache_key="${key}|${block}"
    
    local old_val="${CONFIG_CACHE["$cache_key"]:-}"
    LAST_WRITE_CHANGED=0
    
    if [[ "$old_val" == "$new_val" ]]; then
        return 0
    fi

    # We update the cache *before* building composite commands to ensure we
    # read the updated value when gathering the full state context.
    CONFIG_CACHE["$cache_key"]="$new_val"

    local cmd=()
    case "$block" in
        profile_active) cmd=(asusctl profile set "$new_val") ;;
        profile_ac)     cmd=(asusctl profile set -a "$new_val") ;;
        profile_bat)    cmd=(asusctl profile set -b "$new_val") ;;
        battery_limit)  cmd=(asusctl battery limit "$new_val") ;;
        leds)           cmd=(asusctl leds set "${new_val,,}") ;;
        armoury)        cmd=(asusctl armoury set "$key" "$new_val") ;;
        
# --- Composite Action: Aura Lighting ---
        aura)
            local eff="${CONFIG_CACHE["effect|aura"]:-static}"
            local col_name="${CONFIG_CACHE["color|aura"]:-White}"
            local spd="${CONFIG_CACHE["speed|aura"]:-med}"
            
            # Convert friendly name to hex
            local hex="ffffff"
            case "$col_name" in
                Red) hex="ff0000" ;; Green) hex="00ff00" ;; Blue) hex="0000ff" ;;
                White) hex="ffffff" ;; Yellow) hex="ffff00" ;; Cyan) hex="00ffff" ;;
                Magenta) hex="ff00ff" ;;
            esac

            cmd=(asusctl aura effect "$eff")
            case "$eff" in
                static|pulse|comet|flash) 
                    cmd+=(-c "$hex") 
                    ;;
                breathe|stars)      
                    cmd+=(--colour "$hex" --colour2 "000000" --speed "$spd") 
                    ;;
                rainbow-cycle|rain) 
                    cmd+=(--speed "$spd") 
                    ;;
                rainbow-wave) 
                    # rainbow-wave REQUIRES a direction argument to not fail
                    cmd+=(--direction "right" --speed "$spd") 
                    ;;
                highlight|laser|ripple)
                    cmd+=(-c "$hex" --speed "$spd")
                    ;;
            esac
            ;;
            
        # --- Composite Action: Aura Power States ---
        aura_power)
            local b_awake="${CONFIG_CACHE["awake|aura_power"]:-true}"
            local b_boot="${CONFIG_CACHE["boot|aura_power"]:-true}"
            local b_sleep="${CONFIG_CACHE["sleep|aura_power"]:-false}"
            local b_shut="${CONFIG_CACHE["shutdown|aura_power"]:-false}"

            cmd=(asusctl aura power keyboard)
            [[ "$b_awake" == "true" ]] && cmd+=(--awake)
            [[ "$b_boot" == "true" ]] && cmd+=(--boot)
            [[ "$b_sleep" == "true" ]] && cmd+=(--sleep)
            [[ "$b_shut" == "true" ]] && cmd+=(--shutdown)
            ;;
            
        *) set_status "Unknown block: $block"; return 1 ;;
    esac

# --- Special Case: GPU MUX & dGPU Power ---
    # These ACPI calls take 8-15 seconds to unbind the PCI device and often 
    # throw a false 'os error 5' on DBus when the GPU disappears. 
    # We must background the task to prevent TUI freezing and assume optimistic success.
    if [[ "$key" == "dgpu_disable" || "$key" == "gpu_mux_mode" ]]; then
        ( "${cmd[@]}" >/dev/null 2>&1 & )
        LAST_WRITE_CHANGED=1
        set_status "Hardware toggling... (Takes ~15s to apply)"
        return 0
    fi

    # --- Standard Execution for all other commands ---
    if "${cmd[@]}" >/dev/null 2>&1; then
        LAST_WRITE_CHANGED=1
        return 0
    else
        # Rollback cache if command genuinely fails
        CONFIG_CACHE["$cache_key"]="$old_val"
        set_status "Command failed: ${cmd[*]}"
        return 1
    fi
}

# --- Context Helpers ---

get_active_context() {
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
    local item key type block cache_key

    for item in "${_lav_items_ref[@]}"; do
        IFS='|' read -r key type block _ _ _ <<< "${ITEM_MAP["${REPLY_CTX}::${item}"]}"
        cache_key="${key}|${block}"
        if [[ -n "${CONFIG_CACHE["$cache_key"]+_}" ]]; then
            VALUE_CACHE["${REPLY_CTX}::${item}"]="${CONFIG_CACHE["$cache_key"]}"
        else
            VALUE_CACHE["${REPLY_CTX}::${item}"]="$UNSET_MARKER"
        fi
    done
}

modify_value() {
    local label="$1"
    local -i direction=$2
    local REPLY_REF REPLY_CTX
    get_active_context

    local key type block min max step current new_val
    IFS='|' read -r key type block min max step <<< "${ITEM_MAP["${REPLY_CTX}::${label}"]}"
    current="${VALUE_CACHE["${REPLY_CTX}::${label}"]:-}"

    if [[ "$current" == "$UNSET_MARKER" || -z "$current" ]]; then
        current="${DEFAULTS["${REPLY_CTX}::${label}"]:-}"
        [[ -z "$current" ]] && current="${min:-0}"
    fi

    case "$type" in
        int)
            if [[ ! "$current" =~ ^-?[0-9]+$ ]]; then current="${min:-0}"; fi

            local -i int_val=0
            local _stripped="${current#-}"
            if [[ -n "$_stripped" ]]; then int_val=$(( 10#$_stripped )); fi
            if [[ "$current" == -* ]]; then int_val=$(( -int_val )); fi

            local -i int_step=${step:-1}
            int_val=$(( int_val + direction * int_step ))

            if [[ -n "$min" ]]; then
                local -i min_i
                local _min_s="${min#-}"
                min_i=$(( 10#${_min_s:-0} ))
                [[ "$min" == -* ]] && min_i=$(( -min_i ))
                if (( int_val < min_i )); then int_val=$min_i; fi
            fi
            if [[ -n "$max" ]]; then
                local -i max_i
                local _max_s="${max#-}"
                max_i=$(( 10#${_max_s:-0} ))
                [[ "$max" == -* ]] && max_i=$(( -max_i ))
                if (( int_val > max_i )); then int_val=$max_i; fi
            fi
            new_val=$int_val
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
            new_val="${opts[idx]}"
            ;;
        menu) return 0 ;;
        *) return 0 ;;
    esac

    if write_to_asusctl "$key" "$new_val" "$block"; then
        VALUE_CACHE["${REPLY_CTX}::${label}"]="$new_val"
        clear_status
        if (( LAST_WRITE_CHANGED )); then
            post_write_action
        fi
    fi
}

set_absolute_value() {
    local label="$1" new_val="$2"
    local REPLY_REF REPLY_CTX
    get_active_context
    local key type block
    IFS='|' read -r key type block _ _ _ <<< "${ITEM_MAP["${REPLY_CTX}::${label}"]}"
    if write_to_asusctl "$key" "$new_val" "$block"; then
        VALUE_CACHE["${REPLY_CTX}::${label}"]="$new_val"
        return 0
    fi
    return 1
}

reset_defaults() {
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _rd_items_ref="$REPLY_REF"
    local item def_val
    local -i any_written=0 any_failed=0

    for item in "${_rd_items_ref[@]}"; do
        def_val="${DEFAULTS["${REPLY_CTX}::${item}"]:-}"
        if [[ -n "$def_val" ]]; then
            if set_absolute_value "$item" "$def_val"; then
                if (( LAST_WRITE_CHANGED )); then any_written=1; fi
            else
                any_failed=1
            fi
        fi
    done

    if (( any_written )); then post_write_action; fi
    if (( any_failed )); then set_status "Some defaults failed to apply."; else clear_status; fi
    return 0
}

# --- UI Rendering Engine ---

compute_scroll_window() {
    local -i count=$1
    if (( count == 0 )); then
        SELECTED_ROW=0; SCROLL_OFFSET=0; _vis_start=0; _vis_end=0
        return
    fi
    if (( SELECTED_ROW < 0 )); then SELECTED_ROW=0; fi
    if (( SELECTED_ROW >= count )); then SELECTED_ROW=$(( count - 1 )); fi
    if (( SELECTED_ROW < SCROLL_OFFSET )); then
        SCROLL_OFFSET=$SELECTED_ROW
    elif (( SELECTED_ROW >= SCROLL_OFFSET + MAX_DISPLAY_ROWS )); then
        SCROLL_OFFSET=$(( SELECTED_ROW - MAX_DISPLAY_ROWS + 1 ))
    fi
    local -i max_scroll=$(( count - MAX_DISPLAY_ROWS ))
    if (( max_scroll < 0 )); then max_scroll=0; fi
    if (( SCROLL_OFFSET > max_scroll )); then SCROLL_OFFSET=$max_scroll; fi
    _vis_start=$SCROLL_OFFSET
    _vis_end=$(( SCROLL_OFFSET + MAX_DISPLAY_ROWS ))
    if (( _vis_end > count )); then _vis_end=$count; fi
}

render_scroll_indicator() {
    local -n _rsi_buf=$1
    local position="$2"
    local -i count=$3 boundary=$4

    if [[ "$position" == "above" ]]; then
        if (( SCROLL_OFFSET > 0 )); then
            _rsi_buf+="${C_GREY}    ▲ (more above)${CLR_EOL}${C_RESET}"$'\n'
        else
            _rsi_buf+="${CLR_EOL}"$'\n'
        fi
    else
        if (( count > MAX_DISPLAY_ROWS )); then
            local position_info="[$(( SELECTED_ROW + 1 ))/${count}]"
            if (( boundary < count )); then
                _rsi_buf+="${C_GREY}    ▼ (more below) ${position_info}${CLR_EOL}${C_RESET}"$'\n'
            else
                _rsi_buf+="${C_GREY}                   ${position_info}${CLR_EOL}${C_RESET}"$'\n'
            fi
        else
            _rsi_buf+="${CLR_EOL}"$'\n'
        fi
    fi
}

render_item_list() {
    local -n _ril_buf=$1
    local -n _ril_items=$2
    local _ril_ctx="$3"
    local -i _ril_vs=$4 _ril_ve=$5

    local -i ri
    local item val display type config padded_item

    for (( ri = _ril_vs; ri < _ril_ve; ri++ )); do
        item="${_ril_items[ri]}"
        val="${VALUE_CACHE["${_ril_ctx}::${item}"]:-${UNSET_MARKER}}"
        config="${ITEM_MAP["${_ril_ctx}::${item}"]}"
        IFS='|' read -r _ type _ _ _ _ <<< "$config"

        case "$type" in
            menu)
                display="${C_YELLOW}[+] Open Menu ...${C_RESET}"
                ;;
            *)
                case "$val" in
                    true)            display="${C_GREEN}ON${C_RESET}" ;;
                    false)           display="${C_RED}OFF${C_RESET}" ;;
                    "$UNSET_MARKER") display="${C_YELLOW}⚠ UNSET${C_RESET}" ;;
                    *)               display="${C_WHITE}${val}${C_RESET}" ;;
                esac
                ;;
        esac

        local -i max_len=$(( ITEM_PADDING - 1 ))
        if (( ${#item} > ITEM_PADDING )); then
            printf -v padded_item "%-${max_len}s…" "${item:0:max_len}"
        else
            printf -v padded_item "%-${ITEM_PADDING}s" "$item"
        fi

        if (( ri == SELECTED_ROW )); then
            _ril_buf+="${C_CYAN} ➤ ${C_INVERSE}${padded_item}${C_RESET} : ${display}${CLR_EOL}"$'\n'
        else
            _ril_buf+="    ${padded_item} : ${display}${CLR_EOL}"$'\n'
        fi
    done

    local -i rows_rendered=$(( _ril_ve - _ril_vs ))
    for (( ri = rows_rendered; ri < MAX_DISPLAY_ROWS; ri++ )); do
        _ril_buf+="${CLR_EOL}"$'\n'
    done
}

draw_main_view() {
    local buf="" pad_buf=""
    local -i i current_col=3 zone_start count
    local -i left_pad right_pad vis_len
    local -i _vis_start _vis_end

    buf+="${CURSOR_HOME}"
    buf+="${C_MAGENTA}┌${H_LINE}┐${C_RESET}${CLR_EOL}"$'\n'

    strip_ansi "$APP_TITLE"; local -i t_len=${#REPLY}
    strip_ansi "$APP_VERSION"; local -i v_len=${#REPLY}
    vis_len=$(( t_len + v_len + 1 ))
    left_pad=$(( (BOX_INNER_WIDTH - vis_len) / 2 ))
    right_pad=$(( BOX_INNER_WIDTH - vis_len - left_pad ))

    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad_buf}${C_WHITE}${APP_TITLE} ${C_CYAN}${APP_VERSION}${C_MAGENTA}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}│${C_RESET}${CLR_EOL}"$'\n'

    if (( TAB_SCROLL_START > CURRENT_TAB )); then TAB_SCROLL_START=$CURRENT_TAB; fi
    if (( TAB_SCROLL_START < 0 )); then TAB_SCROLL_START=0; fi

    local tab_line
    local -i max_tab_width=$(( BOX_INNER_WIDTH - 6 ))

    LEFT_ARROW_ZONE=""
    RIGHT_ARROW_ZONE=""

    while true; do
        tab_line="${C_MAGENTA}│ "
        current_col=3
        TAB_ZONES=()
        local -i used_len=0

        if (( TAB_SCROLL_START > 0 )); then
            tab_line+="${C_YELLOW}«${C_RESET} "
            LEFT_ARROW_ZONE="$current_col:$(( current_col + 1 ))"
            used_len=$(( used_len + 2 ))
            current_col=$(( current_col + 2 ))
        else
            tab_line+="  "
            used_len=$(( used_len + 2 ))
            current_col=$(( current_col + 2 ))
        fi

        for (( i = TAB_SCROLL_START; i < TAB_COUNT; i++ )); do
            local name="${TABS[i]}"
            local display_name="$name"
            local -i tab_name_len=${#name}
            local -i chunk_len=$(( tab_name_len + 4 ))
            local -i reserve=0

            if (( i < TAB_COUNT - 1 )); then reserve=2; fi

            if (( used_len + chunk_len + reserve > max_tab_width )); then
                if (( i < CURRENT_TAB || (i == CURRENT_TAB && TAB_SCROLL_START < CURRENT_TAB) )); then
                    TAB_SCROLL_START=$(( TAB_SCROLL_START + 1 ))
                    continue 2
                fi

                if (( i == CURRENT_TAB )); then
                    local -i avail_label=$(( max_tab_width - used_len - reserve - 4 ))
                    if (( avail_label < 1 )); then avail_label=1; fi

                    if (( tab_name_len > avail_label )); then
                        if (( avail_label == 1 )); then display_name="…"; else display_name="${name:0:avail_label-1}…"; fi
                        tab_name_len=${#display_name}
                        chunk_len=$(( tab_name_len + 4 ))
                    fi

                    zone_start=$current_col
                    tab_line+="${C_CYAN}${C_INVERSE} ${display_name} ${C_RESET}${C_MAGENTA}│ "
                    TAB_ZONES+=("${zone_start}:$(( zone_start + tab_name_len + 1 ))")
                    used_len=$(( used_len + chunk_len ))
                    current_col=$(( current_col + chunk_len ))

                    if (( i < TAB_COUNT - 1 )); then
                        tab_line+="${C_YELLOW}» ${C_RESET}"
                        RIGHT_ARROW_ZONE="$current_col:$(( current_col + 1 ))"
                        used_len=$(( used_len + 2 ))
                    fi
                    break
                fi

                tab_line+="${C_YELLOW}» ${C_RESET}"
                RIGHT_ARROW_ZONE="$current_col:$(( current_col + 1 ))"
                used_len=$(( used_len + 2 ))
                break
            fi

            zone_start=$current_col
            if (( i == CURRENT_TAB )); then
                tab_line+="${C_CYAN}${C_INVERSE} ${display_name} ${C_RESET}${C_MAGENTA}│ "
            else
                tab_line+="${C_GREY} ${display_name} ${C_MAGENTA}│ "
            fi

            TAB_ZONES+=("${zone_start}:$(( zone_start + tab_name_len + 1 ))")
            used_len=$(( used_len + chunk_len ))
            current_col=$(( current_col + chunk_len ))
        done

        local -i pad=$(( BOX_INNER_WIDTH - used_len - 1 ))
        if (( pad > 0 )); then
            printf -v pad_buf '%*s' "$pad" ''
            tab_line+="$pad_buf"
        fi

        tab_line+="${C_MAGENTA}│${C_RESET}"
        break
    done

    buf+="${tab_line}${CLR_EOL}"$'\n'
    buf+="${C_MAGENTA}└${H_LINE}┘${C_RESET}${CLR_EOL}"$'\n'

    local items_var="TAB_ITEMS_${CURRENT_TAB}"
    local -n _draw_items_ref="$items_var"
    count=${#_draw_items_ref[@]}

    compute_scroll_window "$count"
    render_scroll_indicator buf "above" "$count" "$_vis_start"
    render_item_list buf _draw_items_ref "${CURRENT_TAB}" "$_vis_start" "$_vis_end"
    render_scroll_indicator buf "below" "$count" "$_vis_end"

    buf+=$'\n'"${C_CYAN} [Tab] Category  [r] Reset  [←/→ h/l] Adjust  [Enter] Action  [q] Quit${C_RESET}${CLR_EOL}"$'\n'
    if [[ -n "$STATUS_MESSAGE" ]]; then
        buf+="${C_CYAN} Status: ${C_RED}${STATUS_MESSAGE}${C_RESET}${CLR_EOL}${CLR_EOS}"
    else
        buf+="${C_CYAN} Target: ${C_WHITE}Daemon (asusd)${C_RESET}${CLR_EOL}${CLR_EOS}"
    fi
    printf '%s' "$buf"
}

draw_detail_view() {
    local buf="" pad_buf=""
    local -i count pad_needed left_pad right_pad vis_len
    local -i _vis_start _vis_end

    buf+="${CURSOR_HOME}"
    buf+="${C_MAGENTA}┌${H_LINE}┐${C_RESET}${CLR_EOL}"$'\n'

    local title=" DETAIL VIEW "
    local sub=" ${CURRENT_MENU_ID} "
    strip_ansi "$title"; local -i t_len=${#REPLY}
    strip_ansi "$sub"; local -i s_len=${#REPLY}
    vis_len=$(( t_len + s_len ))
    left_pad=$(( (BOX_INNER_WIDTH - vis_len) / 2 ))
    right_pad=$(( BOX_INNER_WIDTH - vis_len - left_pad ))

    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad_buf}${C_YELLOW}${title}${C_GREY}${sub}${C_MAGENTA}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}│${C_RESET}${CLR_EOL}"$'\n'

    local breadcrumb=" « Back to ${TABS[CURRENT_TAB]}"
    strip_ansi "$breadcrumb"; local -i b_len=${#REPLY}
    pad_needed=$(( BOX_INNER_WIDTH - b_len ))
    if (( pad_needed < 0 )); then pad_needed=0; fi

    printf -v pad_buf '%*s' "$pad_needed" ''
    buf+="${C_MAGENTA}│${C_CYAN}${breadcrumb}${C_RESET}${pad_buf}${C_MAGENTA}│${C_RESET}${CLR_EOL}"$'\n'
    buf+="${C_MAGENTA}└${H_LINE}┘${C_RESET}${CLR_EOL}"$'\n'

    local items_var="SUBMENU_ITEMS_${CURRENT_MENU_ID}"
    local -n _detail_items_ref="$items_var"
    count=${#_detail_items_ref[@]}

    compute_scroll_window "$count"
    render_scroll_indicator buf "above" "$count" "$_vis_start"
    render_item_list buf _detail_items_ref "${CURRENT_MENU_ID}" "$_vis_start" "$_vis_end"
    render_scroll_indicator buf "below" "$count" "$_vis_end"

    buf+=$'\n'"${C_CYAN} [Esc/Sh+Tab] Back  [r] Reset  [←/→ h/l] Adjust  [Enter] Toggle  [q] Quit${C_RESET}${CLR_EOL}"$'\n'
    if [[ -n "$STATUS_MESSAGE" ]]; then
        buf+="${C_CYAN} Status: ${C_RED}${STATUS_MESSAGE}${C_RESET}${CLR_EOL}${CLR_EOS}"
    else
        buf+="${C_CYAN} Submenu: ${C_WHITE}${CURRENT_MENU_ID}${C_RESET}${CLR_EOL}${CLR_EOS}"
    fi
    printf '%s' "$buf"
}

draw_ui() {
    update_terminal_size
    if ! terminal_size_ok; then
        draw_small_terminal_notice
        return
    fi
    case $CURRENT_VIEW in
        0) draw_main_view ;;
        1) draw_detail_view ;;
    esac
}

# --- Input Handling & Routing ---

navigate() {
    local -i dir=$1
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _nav_items_ref="$REPLY_REF"
    local -i count=${#_nav_items_ref[@]}
    if (( count == 0 )); then return 0; fi
    SELECTED_ROW=$(( (SELECTED_ROW + dir + count) % count ))
}

navigate_page() {
    local -i dir=$1
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _navp_items_ref="$REPLY_REF"
    local -i count=${#_navp_items_ref[@]}
    if (( count == 0 )); then return 0; fi
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
    if (( count == 0 )); then return 0; fi
    if (( target == 0 )); then
        SELECTED_ROW=0
    else
        SELECTED_ROW=$(( count - 1 ))
    fi
}

adjust() {
    local -i dir=$1
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _adj_items_ref="$REPLY_REF"
    if (( ${#_adj_items_ref[@]} == 0 )); then return 0; fi
    modify_value "${_adj_items_ref[SELECTED_ROW]}" "$dir"
}

switch_tab() {
    local -i dir=${1:-1}
    CURRENT_TAB=$(( (CURRENT_TAB + dir + TAB_COUNT) % TAB_COUNT ))
    SELECTED_ROW=0; SCROLL_OFFSET=0
    load_active_values
}

set_tab() {
    local -i idx=$1
    if (( idx != CURRENT_TAB && idx >= 0 && idx < TAB_COUNT )); then
        CURRENT_TAB=$idx
        SELECTED_ROW=0; SCROLL_OFFSET=0
        load_active_values
    fi
}

check_drilldown() {
    local -n _dd_items_ref="TAB_ITEMS_${CURRENT_TAB}"
    if (( ${#_dd_items_ref[@]} == 0 )); then return 1; fi

    local item="${_dd_items_ref[SELECTED_ROW]}"
    local config="${ITEM_MAP["${CURRENT_TAB}::${item}"]}"
    local key type
    IFS='|' read -r key type _ _ _ _ <<< "$config"

    if [[ "$type" == "menu" ]]; then
        PARENT_ROW=$SELECTED_ROW; PARENT_SCROLL=$SCROLL_OFFSET
        CURRENT_MENU_ID="$key"; CURRENT_VIEW=1; SELECTED_ROW=0; SCROLL_OFFSET=0
        load_active_values
        return 0
    fi
    return 1
}

go_back() {
    CURRENT_VIEW=0; SELECTED_ROW=$PARENT_ROW; SCROLL_OFFSET=$PARENT_SCROLL
    load_active_values
}

handle_mouse() {
    local input="$1"
    local -i button x y i start end
    local zone body="${input#'[<'}"

    if [[ "$body" == "$input" ]]; then return 0; fi

    local terminator="${body: -1}"
    if [[ "$terminator" != "M" && "$terminator" != "m" ]]; then return 0; fi

    body="${body%[Mm]}"
    local field1 field2 field3
    IFS=';' read -r field1 field2 field3 <<< "$body"
    if [[ ! "$field1" =~ ^[0-9]+$ ]] || [[ ! "$field2" =~ ^[0-9]+$ ]] || [[ ! "$field3" =~ ^[0-9]+$ ]]; then return 0; fi

    button=$field1; x=$field2; y=$field3

    if (( button == 64 )); then navigate -1; return 0; fi
    if (( button == 65 )); then navigate 1; return 0; fi
    if [[ "$terminator" != "M" ]]; then return 0; fi

    if (( y == TAB_ROW )); then
        if (( CURRENT_VIEW == 0 )); then
            if [[ -n "$LEFT_ARROW_ZONE" ]]; then
                start="${LEFT_ARROW_ZONE%%:*}"; end="${LEFT_ARROW_ZONE##*:}"
                if (( x >= start && x <= end )); then switch_tab -1; return 0; fi
            fi
            if [[ -n "$RIGHT_ARROW_ZONE" ]]; then
                start="${RIGHT_ARROW_ZONE%%:*}"; end="${RIGHT_ARROW_ZONE##*:}"
                if (( x >= start && x <= end )); then switch_tab 1; return 0; fi
            fi
            for (( i = 0; i < TAB_COUNT; i++ )); do
                if [[ -z "${TAB_ZONES[i]:-}" ]]; then continue; fi
                zone="${TAB_ZONES[i]}"; start="${zone%%:*}"; end="${zone##*:}"
                if (( x >= start && x <= end )); then set_tab "$(( i + TAB_SCROLL_START ))"; return 0; fi
            done
        else
            go_back; return 0
        fi
    fi

    local -i effective_start=$(( ITEM_START_ROW + 1 ))
    if (( y >= effective_start && y < effective_start + MAX_DISPLAY_ROWS )); then
        local -i clicked_idx=$(( y - effective_start + SCROLL_OFFSET ))
        local _target_var_name
        if (( CURRENT_VIEW == 0 )); then _target_var_name="TAB_ITEMS_${CURRENT_TAB}"; else _target_var_name="SUBMENU_ITEMS_${CURRENT_MENU_ID}"; fi
        local -n _mouse_items_ref="$_target_var_name"
        
        if (( clicked_idx >= 0 && clicked_idx < ${#_mouse_items_ref[@]} )); then
            SELECTED_ROW=$clicked_idx
            if (( x > ADJUST_THRESHOLD )); then
                if (( button == 0 )); then
                    if (( CURRENT_VIEW == 0 )); then check_drilldown || adjust 1; else adjust 1; fi
                else
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

    if ! IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char; then return 1; fi
    _esc_out+="$char"
    if [[ "$char" == '[' || "$char" == 'O' ]]; then
        while IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char; do
            _esc_out+="$char"
            if [[ "$char" =~ [a-zA-Z~] ]]; then break; fi
        done
    fi
    return 0
}

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
        k|K)               navigate -1 ;;
        j|J)               navigate 1 ;;
        l|L)               adjust 1 ;;
        h|H)               adjust -1 ;;
        g)                 navigate_end 0 ;;
        G)                 navigate_end 1 ;;
        $'\t')             switch_tab 1 ;;
        r|R)               reset_defaults ;;
        ''|$'\n')          check_drilldown || adjust 1 ;;
        $'\x7f'|$'\x08'|$'\e\n') adjust -1 ;;
        q|Q|$'\x03')       exit 0 ;;
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
        '[Z')                go_back; return ;;
        '['*'<'*[Mm])        handle_mouse "$key"; return ;;
    esac
    case "$key" in
        ESC)               go_back ;;
        k|K)               navigate -1 ;;
        j|J)               navigate 1 ;;
        l|L)               adjust 1 ;;
        h|H)               adjust -1 ;;
        g)                 navigate_end 0 ;;
        G)                 navigate_end 1 ;;
        r|R)               reset_defaults ;;
        ''|$'\n')          adjust 1 ;;
        $'\x7f'|$'\x08'|$'\e\n') adjust -1 ;;
        q|Q|$'\x03')       exit 0 ;;
    esac
}

handle_input_router() {
    local key="$1" escape_seq=""
    if [[ "$key" == $'\x1b' ]]; then
        if read_escape_seq escape_seq; then
            key="$escape_seq"
            if [[ "$key" == "" || "$key" == $'\n' ]]; then key=$'\e\n'; fi
        else
            key="ESC"
        fi
    fi
    if ! terminal_size_ok; then
        case "$key" in q|Q|$'\x03') exit 0 ;; esac
        return 0
    fi
    case $CURRENT_VIEW in
        0) handle_key_main "$key" ;;
        1) handle_key_detail "$key" ;;
    esac
}

main() {
    if (( BASH_VERSINFO[0] < 5 )); then
        log_err "Bash 5.0+ required"
        exit 1
    fi
    if [[ ! -t 0 ]]; then
        log_err "TTY required"
        exit 1
    fi

    if ! command -v asusctl &>/dev/null; then
        log_err "Missing dependency: asusctl"
        exit 1
    fi

    # Ensure asusd config directory exists (required for asusd service)
    if [[ ! -d /etc/asusd ]]; then
        mkdir -p /etc/asusd 2>/dev/null || true
    fi

    # Ensure asusd is running
    if ! systemctl is-active --quiet asusd.service 2>/dev/null; then
        systemctl start asusd.service 2>/dev/null || true
    fi

    register_items

    # Show a loading message before the blocking hardware queries begin
    printf "\n\033[1;36m Querying ASUS hardware... (This may take 5 to 20 seconds)\033[0m\n"
    populate_config_cache

    ORIGINAL_STTY=$(stty -g 2>/dev/null) || ORIGINAL_STTY=""
    stty -icanon -echo min 1 time 0 2>/dev/null

    printf '%s%s%s%s' "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"
    load_active_values

    trap 'draw_ui' WINCH

    local key
    while true; do
        draw_ui
        if ! IFS= read -rsn1 key; then continue; fi
        handle_input_router "$key"
    done
}

main "$@"
