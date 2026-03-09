#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Dusky TUI Engine - Battery Notification Configurator
# -----------------------------------------------------------------------------
# Target: ~/user_scripts/battery/notify/battery_notify.sh
# Type: Bash Parameter Expansion Injector
# Reference Engine: v3.9.6 (Gold Standard)
# -----------------------------------------------------------------------------

set -euo pipefail
shopt -s extglob

# =============================================================================
# ▼ USER CONFIGURATION ▼
# =============================================================================

readonly CONFIG_FILE="${HOME}/user_scripts/battery/notify/battery_notify.sh"
readonly SERVICE_NAME="battery_notify.service"
readonly APP_TITLE="Dusky Battery Notif"
readonly APP_VERSION="v1.2.0 (Optimized)"

# Dimensions
declare -ri MAX_DISPLAY_ROWS=12
declare -ri BOX_INNER_WIDTH=76
declare -ri ADJUST_THRESHOLD=40
declare -ri ITEM_PADDING=34

# Layout constants
declare -ri HEADER_ROWS=4
declare -ri TAB_ROW=3
declare -ri ITEM_START_ROW=$(( HEADER_ROWS + 1 ))

readonly -a TABS=("Thresholds" "Timers" "System")

# -----------------------------------------------------------------------------
# Item Registration
# -----------------------------------------------------------------------------
register_items() {
    # --- Tab 0: Thresholds ---
    register 0 "Full Threshold (%)"     'BATTERY_FULL_THRESHOLD|int||80|100|1' "100"
    register 0 "Low Threshold (%)"      'BATTERY_LOW_THRESHOLD|int||15|50|1'   "20"
    register 0 "Critical Threshold (%)" 'BATTERY_CRITICAL_THRESHOLD|int||2|15|1' "10"
    register 0 "Unplug Notify Limit (%)" 'BATTERY_UNPLUG_THRESHOLD|int||0|100|5' "100"

    # --- Tab 1: Timers ---
    register 1 "Repeat: Full (min)"     'REPEAT_FULL_MIN|int||10|1440|10'      "999"
    register 1 "Repeat: Low (min)"      'REPEAT_LOW_MIN|int||1|60|1'           "3"
    register 1 "Repeat: Critical (min)" 'REPEAT_CRITICAL_MIN|int||1|10|1'      "1"
    register 1 "Safety Poll (sec)"      'SAFETY_POLL_INTERVAL|int||10|300|10'  "60"

    # --- Tab 2: System / Actions ---
    register 2 "Suspend Grace (sec)"    'SUSPEND_GRACE_SEC|int||15|300|15'     "60"
    register 2 "Critical Action"        'CMD_CRITICAL|cycle||systemctl suspend,systemctl hibernate,poweroff,loginctl lock-session||' "systemctl suspend"
}

# -----------------------------------------------------------------------------
# Post-Write Hook: Restart the Service
# -----------------------------------------------------------------------------
post_write_action() {
    if systemctl --user is-active --quiet "$SERVICE_NAME"; then
        systemctl --user restart "$SERVICE_NAME"
    fi
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

# Increased timeout for SSH/remote reliability
readonly ESC_READ_TIMEOUT=0.10
readonly UNSET_MARKER='«unset»'

# --- State Management ---
declare -i SELECTED_ROW=0
declare -i CURRENT_TAB=0
declare -i SCROLL_OFFSET=0
declare -ri TAB_COUNT=${#TABS[@]}
declare -a TAB_ZONES=()
declare -i TAB_SCROLL_START=0
declare ORIGINAL_STTY=""

# View State (Support for future submenus, currently Main only)
declare -i CURRENT_VIEW=0 
declare CURRENT_MENU_ID=""
declare -i PARENT_ROW=0
declare -i PARENT_SCROLL=0

# Temp file global
declare _TMPFILE=""

# --- Click Zones ---
declare LEFT_ARROW_ZONE=""
declare RIGHT_ARROW_ZONE=""

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
    # Secure temp file cleanup
    if [[ -n "${_TMPFILE:-}" && -f "$_TMPFILE" ]]; then
        rm -f "$_TMPFILE" 2>/dev/null || :
    fi
    printf '\n' 2>/dev/null || :
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# --- String Helpers ---

strip_ansi() {
    local v="$1"
    # Strip CSI: ESC [ (params) (intermediate) final_byte
    v="${v//$'\033'\[*([0-9;:?<=>])@([@A-Z\[\\\]^_\`a-z\{|\}~])/}"
    REPLY="$v"
}

# --- Core Logic Engine ---

register() {
    local -i tab_idx=$1
    local label="$2" config="$3" default_val="${4:-}"
    local key type block min max step
    IFS='|' read -r key type block min max step <<< "$config"

    ITEM_MAP["${tab_idx}::${label}"]="$config"
    if [[ -n "$default_val" ]]; then DEFAULTS["${tab_idx}::${label}"]="$default_val"; fi
    local -n _reg_tab_ref="TAB_ITEMS_${tab_idx}"
    _reg_tab_ref+=("$label")
}

populate_config_cache() {
    CONFIG_CACHE=()
    local key_part value_part

    # Target Logic: Parse Bash 'readonly VAR="${VAR:-VAL}"'
    while IFS='=' read -r key_part value_part; do
        [[ -z "$key_part" ]] && continue
        CONFIG_CACHE["$key_part"]="$value_part"
    done < <(grep -E '^[[:space:]]*readonly[[:space:]]+[A-Z_]+="\$\{[A-Z_]+:-[^}]+\}"' "$CONFIG_FILE" | \
        sed -E 's/^[[:space:]]*readonly[[:space:]]+([A-Z_]+)="\$\{[A-Z_]+:-(.*)\}"/\1=\2/')
}

# Helper for logic checks
get_cached_int() {
    local key=$1
    local default=$2
    local val=${CONFIG_CACHE["$key"]:-}
    if [[ "$val" =~ ^[0-9]+$ ]]; then
        printf '%d' "$val"
    else
        printf '%d' "$default"
    fi
}

write_value_to_file() {
    local key="$1" new_val="$2" block="${3:-}"
    local current_val="${CONFIG_CACHE["$key"]:-}"
    [[ "$current_val" == "$new_val" ]] && return 0

    # Create temp file
    if [[ -z "$_TMPFILE" ]]; then
        _TMPFILE=$(mktemp "${CONFIG_FILE}.tmp.XXXXXXXXXX")
    fi

    # FIX: Atomic Awk Processing tailored for Bash Variable Injection
    # Pattern: readonly KEY="${KEY:-OLD_VAL}" -> readonly KEY="${KEY:-NEW_VAL}"
    TARGET_KEY="$key" NEW_VALUE="$new_val" \
    LC_ALL=C awk '
    BEGIN {
        target_key = ENVIRON["TARGET_KEY"]
        new_value = ENVIRON["NEW_VALUE"]
        replaced = 0
    }
    {
        line = $0
        # Check if line contains the pattern
        if (line ~ /readonly/ && line ~ target_key && line ~ /"\${/) {
            # Extract key from line to be sure
            if (match(line, /readonly[[:space:]]+([A-Z_]+)=/, matches)) {
                 # Not all awks support matches array, falling back to simpler check
            }
            
            # Construct the specific regex for this key
            # pattern: ^[whitespace]*readonly[whitespace]+KEY="\${KEY:-
            pat = "^[[:space:]]*readonly[[:space:]]+" target_key "=\"\\$\\{" target_key ":-"
            
            if (line ~ pat) {
                # Replacement logic
                # We want to keep everything up to the :- and everything after the }
                
                # Split by the specific bash syntax structure
                split(line, parts, ":-")
                prefix = parts[1] ":-" 
                
                # The rest parts[2] contains "OLD_VAL}"
                # We assume simple values without } inside them for this config type
                sub(/^[^\}]+/, new_value, parts[2])
                
                print prefix parts[2]
                replaced = 1
                next
            }
        }
        print line
    }
    END { exit (replaced ? 0 : 1) }
    ' "$CONFIG_FILE" > "$_TMPFILE" || {
        rm -f "$_TMPFILE" 2>/dev/null || :
        _TMPFILE=""
        return 1
    }

    if [[ ! -s "$_TMPFILE" ]]; then
        rm -f "$_TMPFILE" 2>/dev/null || :
        _TMPFILE=""
        return 1
    fi

    cat "$_TMPFILE" > "$CONFIG_FILE"
    rm -f "$_TMPFILE"
    _TMPFILE=""

    CONFIG_CACHE["$key"]="$new_val"
    return 0
}

# --- Context Helpers ---

get_active_context() {
    REPLY_CTX="${CURRENT_TAB}"
    REPLY_REF="TAB_ITEMS_${CURRENT_TAB}"
}

load_active_values() {
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _lav_items_ref="$REPLY_REF"
    local item key val

    for item in "${_lav_items_ref[@]}"; do
        IFS='|' read -r key _ _ _ _ _ <<< "${ITEM_MAP["${REPLY_CTX}::${item}"]}"
        val="${CONFIG_CACHE["$key"]:-}"
        if [[ -z "$val" ]]; then
            VALUE_CACHE["${REPLY_CTX}::${item}"]="$UNSET_MARKER"
        else
            VALUE_CACHE["${REPLY_CTX}::${item}"]="$val"
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
            # --- INTELLIGENT LOGIC CONSTRAINTS (Battery Specific) ---
            local -i limit_val
            
            if [[ "$key" == "BATTERY_LOW_THRESHOLD" ]]; then
                # Constraint: Must be > Critical
                limit_val=$(get_cached_int "BATTERY_CRITICAL_THRESHOLD" 10)
                if (( min <= limit_val )); then min=$(( limit_val + 1 )); fi
                
                # Constraint: Must be < Full
                limit_val=$(get_cached_int "BATTERY_FULL_THRESHOLD" 100)
                if (( max >= limit_val )); then max=$(( limit_val - 1 )); fi

            elif [[ "$key" == "BATTERY_CRITICAL_THRESHOLD" ]]; then
                # Constraint: Must be < Low
                limit_val=$(get_cached_int "BATTERY_LOW_THRESHOLD" 20)
                if (( max >= limit_val )); then max=$(( limit_val - 1 )); fi

            elif [[ "$key" == "BATTERY_FULL_THRESHOLD" ]]; then
                # Constraint: Must be > Low
                limit_val=$(get_cached_int "BATTERY_LOW_THRESHOLD" 20)
                if (( min <= limit_val )); then min=$(( limit_val + 1 )); fi
            fi
            # --------------------------------------------------------

            if [[ ! "$current" =~ ^-?[0-9]+$ ]]; then current="${min:-0}"; fi
            local -i int_val=0
            local _stripped="${current#-}"
            [[ -n "$_stripped" ]] && int_val=$(( 10#$_stripped ))
            [[ "$current" == -* ]] && int_val=$(( -int_val ))

            local -i int_step=${step:-1}
            int_val=$(( int_val + direction * int_step ))

            if [[ -n "$min" ]]; then
                if (( int_val < min )); then int_val=$min; fi
            fi
            if [[ -n "$max" ]]; then
                if (( int_val > max )); then int_val=$max; fi
            fi
            new_val=$int_val
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
        *) return 0 ;;
    esac

    if write_value_to_file "$key" "$new_val" "$block"; then
        VALUE_CACHE["${REPLY_CTX}::${label}"]="$new_val"
        post_write_action
    fi
}

set_absolute_value() {
    local label="$1" new_val="$2"
    local REPLY_REF REPLY_CTX
    get_active_context
    local key type block
    IFS='|' read -r key type block _ _ _ <<< "${ITEM_MAP["${REPLY_CTX}::${label}"]}"
    if write_value_to_file "$key" "$new_val" "$block"; then
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
    local -i any_written=0
    for item in "${_rd_items_ref[@]}"; do
        def_val="${DEFAULTS["${REPLY_CTX}::${item}"]:-}"
        if [[ -n "$def_val" ]]; then
            if set_absolute_value "$item" "$def_val"; then
                any_written=1
            fi
        fi
    done
    (( any_written )) && post_write_action || :
    return 0
}

# --- UI Rendering Engine ---

compute_scroll_window() {
    local -i count=$1
    if (( count == 0 )); then
        SELECTED_ROW=0; SCROLL_OFFSET=0
        _vis_start=0; _vis_end=0
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
        
        case "$val" in
            true)              display="${C_GREEN}ON${C_RESET}" ;;
            false)             display="${C_RED}OFF${C_RESET}" ;;
            "$UNSET_MARKER")   display="${C_YELLOW}⚠ UNSET${C_RESET}" ;;
            *)                 display="${C_WHITE}${val}${C_RESET}" ;;
        esac

        local max_len=$(( ITEM_PADDING - 1 ))
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

draw_ui() {
    local buf="" pad_buf=""
    local -i i current_col=3 zone_start len count pad_needed
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

    # --- Scrollable Tab Rendering (Sliding Window) ---
    if (( TAB_SCROLL_START > CURRENT_TAB )); then
        TAB_SCROLL_START=$CURRENT_TAB
    fi

    local tab_line
    local -i max_tab_width=$(( BOX_INNER_WIDTH - 6 ))

    LEFT_ARROW_ZONE=""
    RIGHT_ARROW_ZONE=""

    while true; do
        tab_line="${C_MAGENTA}│ "
        current_col=3
        TAB_ZONES=()
        local -i used_len=0

        # Left Arrow
        if (( TAB_SCROLL_START > 0 )); then
            tab_line+="${C_YELLOW}«${C_RESET} "
            LEFT_ARROW_ZONE="$current_col:$((current_col+1))"
            used_len=$(( used_len + 2 ))
            current_col=$(( current_col + 2 ))
        else
            tab_line+="  "
            used_len=$(( used_len + 2 ))
            current_col=$(( current_col + 2 ))
        fi

        for (( i = TAB_SCROLL_START; i < TAB_COUNT; i++ )); do
            local name="${TABS[i]}"
            local t_len=${#name}
            local chunk_len=$(( t_len + 4 ))

            local reserve=0
            if (( i < TAB_COUNT - 1 )); then reserve=2; fi

            if (( used_len + chunk_len + reserve > max_tab_width )); then
                if (( i <= CURRENT_TAB )); then
                    TAB_SCROLL_START=$(( TAB_SCROLL_START + 1 ))
                    continue 2
                fi
                # Right Arrow
                tab_line+="${C_YELLOW}» ${C_RESET}"
                RIGHT_ARROW_ZONE="$current_col:$((current_col+1))"
                used_len=$(( used_len + 2 ))
                break
            fi

            zone_start=$current_col
            if (( i == CURRENT_TAB )); then
                tab_line+="${C_CYAN}${C_INVERSE} ${name} ${C_RESET}${C_MAGENTA}│ "
            else
                tab_line+="${C_GREY} ${name} ${C_MAGENTA}│ "
            fi
            
            TAB_ZONES+=("${zone_start}:$(( zone_start + t_len + 1 ))")
            used_len=$(( used_len + chunk_len ))
            current_col=$(( current_col + chunk_len ))
        done

        local pad=$(( BOX_INNER_WIDTH - used_len - 1 ))
        if (( pad > 0 )); then
            printf -v pad_buf '%*s' "$pad" ''
            tab_line+="$pad_buf"
        fi
        
        tab_line+="${C_MAGENTA}│${C_RESET}"
        break
    done

    buf+="${tab_line}${CLR_EOL}"$'\n'
    buf+="${C_MAGENTA}└${H_LINE}┘${C_RESET}${CLR_EOL}"$'\n'

    # Items
    local items_var="TAB_ITEMS_${CURRENT_TAB}"
    local -n _draw_items_ref="$items_var"
    count=${#_draw_items_ref[@]}

    compute_scroll_window "$count"
    render_scroll_indicator buf "above" "$count" "$_vis_start"
    render_item_list buf _draw_items_ref "${CURRENT_TAB}" "$_vis_start" "$_vis_end"
    render_scroll_indicator buf "below" "$count" "$_vis_end"

    buf+=$'\n'"${C_CYAN} [Tab] Category  [r] Reset  [←/→ h/l] Adjust  [↑/↓ j/k] Nav  [q] Quit${C_RESET}${CLR_EOL}"$'\n'
    buf+="${C_CYAN} File: ${C_WHITE}${CONFIG_FILE}${C_RESET}${CLR_EOL}${CLR_EOS}"
    printf '%s' "$buf"
}

# --- Input Handling ---

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
    if (( target == 0 )); then SELECTED_ROW=0; else SELECTED_ROW=$(( count - 1 )); fi
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

handle_mouse() {
    local input="$1"
    local -i button x y i start end
    local type zone

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
    button=$field1; x=$field2; y=$field3

    if (( button == 64 )); then navigate -1; return 0; fi
    if (( button == 65 )); then navigate 1; return 0; fi
    if [[ "$terminator" != "M" ]]; then return 0; fi

    if (( y == TAB_ROW )); then
        if [[ -n "$LEFT_ARROW_ZONE" ]]; then
            start="${LEFT_ARROW_ZONE%%:*}"
            end="${LEFT_ARROW_ZONE##*:}"
            if (( x >= start && x <= end )); then switch_tab -1; return 0; fi
        fi
        if [[ -n "$RIGHT_ARROW_ZONE" ]]; then
            start="${RIGHT_ARROW_ZONE%%:*}"
            end="${RIGHT_ARROW_ZONE##*:}"
            if (( x >= start && x <= end )); then switch_tab 1; return 0; fi
        fi

        for (( i = 0; i < TAB_COUNT; i++ )); do
            if [[ -z "${TAB_ZONES[i]:-}" ]]; then continue; fi
            zone="${TAB_ZONES[i]}"
            start="${zone%%:*}"
            end="${zone##*:}"
            if (( x >= start && x <= end )); then set_tab "$(( i + TAB_SCROLL_START ))"; return 0; fi
        done
        return 0
    fi

    local -i effective_start=$(( ITEM_START_ROW + 1 ))
    if (( y >= effective_start && y < effective_start + MAX_DISPLAY_ROWS )); then
        local -i clicked_idx=$(( y - effective_start + SCROLL_OFFSET ))
        local -n _mouse_items_ref="TAB_ITEMS_${CURRENT_TAB}"
        local -i count=${#_mouse_items_ref[@]}
        
        if (( clicked_idx >= 0 && clicked_idx < count )); then
            SELECTED_ROW=$clicked_idx
            if (( x > ADJUST_THRESHOLD )); then
                (( button == 0 )) && adjust 1 || adjust -1
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

handle_input_router() {
    local key="$1"
    local escape_seq=""

    if [[ "$key" == $'\x1b' ]]; then
        if read_escape_seq escape_seq; then
            key="$escape_seq"
            [[ "$key" == "" || "$key" == $'\n' ]] && key=$'\e\n'
        else
            key="ESC"
        fi
    fi

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
        ''|$'\n')       adjust 1 ;;
        $'\x7f'|$'\x08'|$'\e\n') adjust -1 ;;
        q|Q|$'\x03')    exit 0 ;;
    esac
}

main() {
    if (( BASH_VERSINFO[0] < 5 )); then log_err "Bash 5.0+ required"; exit 1; fi
    if [[ ! -t 0 ]]; then log_err "TTY required"; exit 1; fi
    if [[ ! -f "$CONFIG_FILE" ]]; then log_err "Config not found: $CONFIG_FILE"; exit 1; fi
    if [[ ! -w "$CONFIG_FILE" ]]; then log_err "Config not writable: $CONFIG_FILE"; exit 1; fi

    command -v awk &>/dev/null || { log_err "Required: awk"; exit 1; }
    command -v grep &>/dev/null || { log_err "Required: grep"; exit 1; }

    register_items
    populate_config_cache

    ORIGINAL_STTY=$(stty -g 2>/dev/null) || ORIGINAL_STTY=""
    stty -icanon -echo min 1 time 0 2>/dev/null

    printf '%s%s%s%s' "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"
    load_active_values

    # Responsive Window Resizing
    trap 'draw_ui' WINCH

    local key
    while true; do
        draw_ui
        if ! IFS= read -rsn1 key; then continue; fi
        handle_input_router "$key"
    done
}

main "$@"
