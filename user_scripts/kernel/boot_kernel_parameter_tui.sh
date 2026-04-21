#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Kernel Cmdline Editor TUI - Based on Dusky TUI Engine
# -----------------------------------------------------------------------------
# Target: Arch Linux / Limine / Kernel Parameters
# -----------------------------------------------------------------------------

set -euo pipefail
shopt -s extglob

# =============================================================================
# ▼ USER CONFIGURATION (EDIT THIS SECTION) ▼
# =============================================================================

# POINT THIS TO YOUR REAL CONFIG FILE
declare -r CONFIG_FILE="/etc/kernel/cmdline"
declare -r APP_TITLE="Kernel Parameter Editor"
declare -r APP_VERSION="v1.0.0"

# Dimensions & Layout
declare -ri MAX_DISPLAY_ROWS=14
declare -ri BOX_INNER_WIDTH=76
declare -ri ADJUST_THRESHOLD=38
declare -ri ITEM_PADDING=32

declare -ri HEADER_ROWS=4
declare -ri TAB_ROW=3
declare -ri ITEM_START_ROW=$(( HEADER_ROWS + 1 ))

declare -ra TABS=("Performance" "Hardware" "Debug" "Misc")

# Item Registration
# Config field format:
#   'key|type|block|min|max|step'
#
# Since cmdline is flat, 'block' is unused but kept for engine compatibility.
# For toggling kernel params on/off, we use 'cycle' with 'unset' as a state.
# Selecting 'unset' cleanly removes the parameter from the file.
register_items() {
    # Tab 0: Performance
    register 0 "Mitigations"         'mitigations|cycle||unset,auto,off||'           "unset"
    register 0 "ZSwap Enabled"       'zswap.enabled|cycle||unset,0,1||'              "unset"
    register 0 "Intel P-State"       'intel_pstate|cycle||unset,disable,passive,force||' "unset"
    register 0 "Trans. Hugepages"    'transparent_hugepage|cycle||unset,always,madvise,never||' "unset"
    register 0 "NUMA Balancing"      'numa_balancing|cycle||unset,enable,disable||'  "unset"
    register 0 "Disable Watchdog"    'nowatchdog|bool||||'                           "false"
    register 0 "Thread IRQs"         'threadirqs|bool||||'                           "false"

    # Tab 1: Hardware
    register 1 "Intel IOMMU"         'intel_iommu|cycle||unset,on,off||'             "unset"
    register 1 "AMD IOMMU"           'amd_iommu|cycle||unset,off,fullflush,force_isolation||' "unset"
    register 1 "IOMMU"               'iommu|cycle||unset,pt,off,force||'             "unset"
    register 1 "PCIE ASPM"           'pcie_aspm|cycle||unset,default,force,off||'    "unset"
    register 1 "USB Autosuspend"     'usbcore.autosuspend|cycle||unset,-1,1||'       "unset"
    register 1 "Mem Limit"           'mem|cycle||unset,4G,8G,16G,32G||'              "unset"
    register 1 "Clock Source"        'clocksource|cycle||unset,tsc,hpet,acpi_pm||'   "unset"
    register 1 "ACPI Backlight"      'acpi_backlight|cycle||unset,video,vendor,native||' "unset"

    # Tab 2: Debug
    register 2 "Log Level"           'loglevel|int||0|7|1'                           "3"
    register 2 "Quiet Boot"          'quiet|bool||||'                                "false"
    register 2 "Initcall Debug"      'initcall_debug|bool||||'                       "false"
    register 2 "Always Enable SysRq" 'sysrq_always_enabled|bool||||'                 "false"
    register 2 "Ignore Loglevel"     'ignore_loglevel|bool||||'                      "false"
    register 2 "Panic Timeout (s)"   'panic|int||-1|60|1'                            "0"

    # Tab 3: Misc
    register 3 "FSCK Mode"           'fsck.mode|cycle||unset,auto,skip||'            "unset"
    register 3 "AppArmor"            'apparmor|cycle||unset,0,1||'                   "unset"
    register 3 "SELinux"             'selinux|cycle||unset,0,1||'                    "unset"
    register 3 "Audit Subsystem"     'audit|cycle||unset,0,1||'                      "unset"
    register 3 "Console Blank (s)"   'consoleblank|int||0|3600|60'                   "600"
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
declare -r UNSET_MARKER='unset' # Replaced visual marker to map smoothly to cycle logic

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

# Temp file globals
declare _TMPFILE=""
declare _TMPMODE=""
declare WRITE_TARGET=""

# Terminal geometry
declare -i TERM_ROWS=0
declare -i TERM_COLS=0
declare -ri MIN_TERM_COLS=$(( BOX_INNER_WIDTH + 2 ))
declare -ri MIN_TERM_ROWS=$(( HEADER_ROWS + MAX_DISPLAY_ROWS + 5 ))

# Write state & Application Exits
declare -g QUIT_REQUESTED=0
declare -g PENDING_BOOTLOADER_UPDATE=0
declare STATUS_MESSAGE=""

# --- Click Zones for Arrows ---
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
    if [[ -n "${_TMPFILE:-}" && -f "$_TMPFILE" ]]; then
        rm -f "$_TMPFILE" 2>/dev/null || :
    fi
    _TMPFILE=""
    _TMPMODE=""
    printf '\n' 2>/dev/null || :
}

trap cleanup EXIT
trap 'QUIT_REQUESTED=1' INT TERM

resolve_write_target() {
    if [[ ! -f "$CONFIG_FILE" ]]; then
        touch "$CONFIG_FILE"
        chmod 644 "$CONFIG_FILE"
    fi
    WRITE_TARGET=$(realpath -e -- "$CONFIG_FILE")
}

create_tmpfile() {
    local target_dir target_base
    target_dir=$(dirname -- "$WRITE_TARGET")
    target_base=$(basename -- "$WRITE_TARGET")

    if ! _TMPFILE=$(mktemp --tmpdir="$target_dir" ".${target_base}.tmp.XXXXXXXXXX" 2>/dev/null); then
        _TMPFILE=""
        _TMPMODE=""
        return 1
    fi
    _TMPMODE="atomic"
    return 0
}

commit_tmpfile() {
    [[ -n "${_TMPFILE:-}" && -f "$_TMPFILE" && "${_TMPMODE:-}" == "atomic" ]] || return 1
    chmod --reference="$WRITE_TARGET" -- "$_TMPFILE" 2>/dev/null || return 1
    mv -f -- "$_TMPFILE" "$WRITE_TARGET" || return 1
    _TMPFILE=""
    _TMPMODE=""
    return 0
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

    ITEM_MAP["${tab_idx}::${label}"]="$config"
    if [[ -n "$default_val" ]]; then
        DEFAULTS["${tab_idx}::${label}"]="$default_val"
    fi

    local -n _reg_tab_ref="TAB_ITEMS_${tab_idx}"
    _reg_tab_ref+=("$label")
}

register_child() {
    local parent_id="$1"
    local label="$2" config="$3" default_val="${4:-}"
    
    if ! declare -p "SUBMENU_ITEMS_${parent_id}" &>/dev/null; then
        declare -ga "SUBMENU_ITEMS_${parent_id}=()"
    fi

    ITEM_MAP["${parent_id}::${label}"]="$config"
    if [[ -n "$default_val" ]]; then
        DEFAULTS["${parent_id}::${label}"]="$default_val"
    fi

    local -n _child_ref="SUBMENU_ITEMS_${parent_id}"
    _child_ref+=("$label")
}

# Heavily modified to parse flat space-separated cmdline strings
populate_config_cache() {
    CONFIG_CACHE=()
    local cmdline=""
    [[ -f "$CONFIG_FILE" ]] && cmdline=$(<"$CONFIG_FILE")
    
    local -a tokens
    read -ra tokens <<< "$cmdline"
    
    for token in "${tokens[@]}"; do
        if [[ "$token" == *"="* ]]; then
            local key="${token%%=*}"
            local val="${token#*=}"
            CONFIG_CACHE["${key}|"]="$val"
        else
            # Flag style arguments (like 'quiet')
            CONFIG_CACHE["${token}|"]="true"
        fi
    done
}

# Heavily modified to manipulate space-separated strings safely
write_value_to_file() {
    local key="$1" new_val="$2" block="${3:-}"
    local cache_key="${key}|${block}"
    local current_val="${CONFIG_CACHE["$cache_key"]:-$UNSET_MARKER}"
    
    if [[ "$current_val" == "$new_val" ]]; then
        return 0
    fi

    create_tmpfile || {
        set_status "Atomic save unavailable."
        return 1
    }

    local cmdline=""
    [[ -f "$CONFIG_FILE" ]] && cmdline=$(<"$CONFIG_FILE")
    
    local -a tokens new_tokens=()
    read -ra tokens <<< "$cmdline"
    
    for token in "${tokens[@]}"; do
        local t_key="${token%%=*}"
        # Omit the old token matching our key
        if [[ "$t_key" == "$key" ]]; then
            continue
        fi
        new_tokens+=("$token")
    done
    
    # Append the newly modified token unless disabled/unset
    if [[ "$new_val" != "$UNSET_MARKER" && "$new_val" != "false" ]]; then
        if [[ "$new_val" == "true" ]]; then
            new_tokens+=("$key")
        else
            new_tokens+=("${key}=${new_val}")
        fi
    fi
    
    echo "${new_tokens[*]}" > "$_TMPFILE"

    commit_tmpfile || {
        set_status "Atomic save failed."
        return 1
    }

    if [[ "$new_val" == "$UNSET_MARKER" || "$new_val" == "false" ]]; then
        unset "CONFIG_CACHE[$cache_key]"
    else
        CONFIG_CACHE["$cache_key"]="$new_val"
    fi
    
    PENDING_BOOTLOADER_UPDATE=1
    return 0
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

    if write_value_to_file "$key" "$new_val" "$block"; then
        VALUE_CACHE["${REPLY_CTX}::${label}"]="$new_val"
        clear_status
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
    local -i any_failed=0

    for item in "${_rd_items_ref[@]}"; do
        def_val="${DEFAULTS["${REPLY_CTX}::${item}"]:-}"
        if [[ -n "$def_val" ]]; then
            if ! set_absolute_value "$item" "$def_val"; then
                any_failed=1
            fi
        fi
    done

    if (( any_failed )); then
        set_status "Some defaults were not written."
    else
        clear_status
    fi
    return 0
}

# --- UI Rendering Engine ---

compute_scroll_window() {
    local -i count=$1
    if (( count == 0 )); then
        SELECTED_ROW=0
        SCROLL_OFFSET=0
        _vis_start=0
        _vis_end=0
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

    if (( TAB_SCROLL_START > CURRENT_TAB )); then
        TAB_SCROLL_START=$CURRENT_TAB
    fi
    if (( TAB_SCROLL_START < 0 )); then
        TAB_SCROLL_START=0
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
                        if (( avail_label == 1 )); then
                            display_name="…"
                        else
                            display_name="${name:0:avail_label-1}…"
                        fi
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
        buf+="${C_CYAN} Target: ${C_WHITE}${CONFIG_FILE}${C_RESET}${CLR_EOL}${CLR_EOS}"
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

check_drilldown() {
    local -n _dd_items_ref="TAB_ITEMS_${CURRENT_TAB}"
    if (( ${#_dd_items_ref[@]} == 0 )); then return 1; fi

    local item="${_dd_items_ref[SELECTED_ROW]}"
    local config="${ITEM_MAP["${CURRENT_TAB}::${item}"]}"
    local key type
    IFS='|' read -r key type _ _ _ _ <<< "$config"

    if [[ "$type" == "menu" ]]; then
        PARENT_ROW=$SELECTED_ROW
        PARENT_SCROLL=$SCROLL_OFFSET
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
    if [[ ! "$field1" =~ ^[0-9]+$ ]] || [[ ! "$field2" =~ ^[0-9]+$ ]] || [[ ! "$field3" =~ ^[0-9]+$ ]]; then return 0; fi

    button=$field1; x=$field2; y=$field3

    if (( button == 64 )); then navigate -1; return 0; fi
    if (( button == 65 )); then navigate 1; return 0; fi
    if [[ "$terminator" != "M" ]]; then return 0; fi

    if (( y == TAB_ROW )); then
        if (( CURRENT_VIEW == 0 )); then
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
        else
            go_back; return 0
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
        q|Q|$'\x03')       QUIT_REQUESTED=1 ;;
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
        q|Q|$'\x03')       QUIT_REQUESTED=1 ;;
    esac
}

handle_input_router() {
    local key="$1"
    local escape_seq=""

    if [[ "$key" == $'\x1b' ]]; then
        if read_escape_seq escape_seq; then
            key="$escape_seq"
            if [[ "$key" == "" || "$key" == $'\n' ]]; then
                key=$'\e\n'
            fi
        else
            key="ESC"
        fi
    fi

    if ! terminal_size_ok; then
        case "$key" in
            q|Q|$'\x03') QUIT_REQUESTED=1 ;;
        esac
        return 0
    fi

    case $CURRENT_VIEW in
        0) handle_key_main "$key" ;;
        1) handle_key_detail "$key" ;;
    esac
}

main() {
    if (( BASH_VERSINFO[0] < 5 )); then log_err "Bash 5.0+ required"; exit 1; fi
    if [[ ! -t 0 ]]; then log_err "TTY required"; exit 1; fi

    if (( EUID != 0 )); then
        printf "%s[*]%s Elevating to root privileges (sudo)...\n" "$C_CYAN" "$C_RESET"
        exec sudo "$0" "$@"
    fi

    local _dep; for _dep in realpath; do
        if ! command -v "$_dep" &>/dev/null; then log_err "Missing dependency: ${_dep}"; exit 1; fi
    done

    resolve_write_target

    if [[ ! -w "$WRITE_TARGET" ]]; then log_err "Config not writable: $CONFIG_FILE"; exit 1; fi

    register_items
    populate_config_cache

    ORIGINAL_STTY=$(stty -g 2>/dev/null) || ORIGINAL_STTY=""
    stty -icanon -echo min 1 time 0 2>/dev/null

    printf '%s%s%s%s' "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"
    load_active_values

    trap 'draw_ui' WINCH

    local key
    while (( ! QUIT_REQUESTED )); do
        draw_ui
        if ! IFS= read -rsn1 key; then continue; fi
        handle_input_router "$key"
    done

    # Let cleanup reset terminal before interacting with the user again
    cleanup
    trap - EXIT INT TERM

    if (( PENDING_BOOTLOADER_UPDATE )); then
        printf "\n%s[+]%s Changes detected in %s\n" "$C_CYAN" "$C_RESET" "$CONFIG_FILE"
        read -p "    Run 'limine-update' to apply to bootloader? [Y/n] " -n 1 -r
        echo
        if [[ ! "$REPLY" =~ ^[Nn]$ ]]; then
            if command -v limine-update >/dev/null 2>&1; then
                limine-update
            else
                printf "%s[ERROR]%s limine-update not found in PATH!\n" "$C_RED" "$C_RESET"
            fi
        fi
    fi
}

main "$@"
