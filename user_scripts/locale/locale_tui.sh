#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Arch System Settings TUI Engine
# Target: Arch Linux, systemd (timedatectl/localectl), Wayland/UWSM sessions
# Derived from the Dusky TUI Engine blueprint.
# Based on Tui 5.2
# -----------------------------------------------------------------------------

set -E -o pipefail
shopt -s extglob

# =============================================================================
# USER CONFIGURATION
# =============================================================================

declare -r APP_TITLE="Dusky System Region Manager"
declare -r APP_VERSION="v1.1.0"

# Dimensions & layout.
declare -ri MAX_DISPLAY_ROWS=14
declare -ri BOX_INNER_WIDTH=76
declare -ri ADJUST_THRESHOLD=38
declare -ri ITEM_PADDING=32

declare -ri HEADER_ROWS=4
declare -ri TAB_ROW=3
declare -ri ITEM_START_ROW=$(( HEADER_ROWS + 1 ))

declare -ra TABS=("Time & Date" "Locales & Input" "System Info")

register_items() {
    # Schema: key|type|block|min|max|step
    # For system settings, 'key' maps to our internal query router.
    
    # Tab 0: Time & Date
    register 0 "Current Date"       'curr_date|info||||'        ""
    register 0 "Current Time"       'curr_time|info||||'        ""
    register 0 "NTP Time Sync"      'ntp|bool||||'              "true"
    register 0 "Set Timezone"       'pick_timezone|action||||'  ""
    register 0 "Set Date (Manual)"  'set_date|action||||'       ""
    register 0 "Set Time (Manual)"  'set_time|action||||'       ""
    register 0 "RTC in Local TZ"    'rtc_local|bool||||'        "false"
    
    # Tab 1: Locales & Input
    register 1 "System Locale"  'pick_locale|action||||'    ""
    register 1 "TTY Keymap"     'pick_keymap|action||||'    ""
    
    # Tab 2: System Info
    register 2 "Hostname"         'sys_host|info||||'         ""
    register 2 "Operating System" 'sys_os|info||||'           ""
    register 2 "Kernel Version"   'sys_kernel|info||||'       ""
    register 2 "Uptime"           'sys_uptime|info||||'       ""
}

# -----------------------------------------------------------------------------
# ACTIONS AND CALLBACKS
# -----------------------------------------------------------------------------

prompt_input() {
    local prompt_text=$1
    local -n var_ref=$2
    
    printf '%s%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" "$C_RESET" 2>/dev/null || :
    [[ -n ${ORIGINAL_STTY:-} ]] && stty "$ORIGINAL_STTY" < /dev/tty 2>/dev/null || :

    printf '%s%s' "$CLR_SCREEN" "$CURSOR_HOME"
    printf '\n  %s┌──────────────────────────────────────────────────┐%s\n' "$C_CYAN" "$C_RESET"
    printf '  %s│%s  Interactive Parameter Input                     %s│%s\n' "$C_CYAN" "$C_WHITE" "$C_CYAN" "$C_RESET"
    printf '  %s└──────────────────────────────────────────────────┘%s\n\n' "$C_CYAN" "$C_RESET"

    local temp_input=""
    read -e -p "  $prompt_text" temp_input < /dev/tty
    var_ref=$temp_input

    stty -icanon -echo -ixon min 0 time 0 < /dev/tty 2>/dev/null || :
    printf '%s%s%s%s' "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"
}

action_set_date() {
    if [[ $(timedatectl show -p NTP --value 2>/dev/null) == "yes" ]]; then
        set_status "Error: Disable NTP Time Sync first."
        return 1
    fi
    
    local new_date=""
    prompt_input "Enter new date (YYYY-MM-DD): " new_date
    
    trim_spaces "$new_date"
    new_date=$REPLY
    
    if [[ -n "$new_date" ]]; then
        if [[ ! "$new_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
            set_status "Failed: Format must be exactly YYYY-MM-DD."
            CURRENT_VIEW=0
            load_active_values
            return 1
        fi
        
        if ! acquire_sudo; then return 1; fi
        local curr_time
        curr_time=$(date +%H:%M:%S)
        set_status "Applying date: $new_date..."
        
        local err_msg
        if err_msg=$(sudo timedatectl set-time "$new_date $curr_time" 2>&1); then
            set_status "Date successfully set to $new_date."
            LAST_WRITE_CHANGED=1
        else
            # systemd protects against setting time before its build date. Bypass via kernel date.
            if sudo date -s "$new_date $curr_time" >/dev/null 2>&1; then
                sudo hwclock --systohc >/dev/null 2>&1 || true
                set_status "Date successfully set to $new_date (via raw override)."
                LAST_WRITE_CHANGED=1
            else
                # Output the actual system error instead of a hardcoded string
                set_status "Failed: $(echo "$err_msg" | head -n 1)"
            fi
        fi
    else
        set_status "Date change cancelled."
    fi
    CURRENT_VIEW=0
    load_active_values
}

action_set_time() {
    if [[ $(timedatectl show -p NTP --value 2>/dev/null) == "yes" ]]; then
        set_status "Error: Disable NTP Time Sync first."
        return 1
    fi
    
    local new_time=""
    prompt_input "Enter new time (HH:MM:SS): " new_time
    
    trim_spaces "$new_time"
    new_time=$REPLY
    
    if [[ -n "$new_time" ]]; then
        if [[ ! "$new_time" =~ ^[0-9]{2}:[0-9]{2}:[0-9]{2}$ ]]; then
            set_status "Failed: Format must be exactly HH:MM:SS."
            CURRENT_VIEW=0
            load_active_values
            return 1
        fi

        if ! acquire_sudo; then return 1; fi
        
        local curr_date
        curr_date=$(date +%Y-%m-%d)
        
        set_status "Applying time: $new_time..."
        
        local err_msg
        if err_msg=$(sudo timedatectl set-time "$curr_date $new_time" 2>&1); then
            set_status "Time successfully set to $new_time."
            LAST_WRITE_CHANGED=1
        else
            # systemd protects against setting time before its build date. Bypass via kernel date.
            if sudo date -s "$curr_date $new_time" >/dev/null 2>&1; then
                sudo hwclock --systohc >/dev/null 2>&1 || true
                set_status "Time successfully set to $new_time (via raw override)."
                LAST_WRITE_CHANGED=1
            else
                set_status "Failed: $(echo "$err_msg" | head -n 1)"
            fi
        fi
    else
        set_status "Time change cancelled."
    fi
    CURRENT_VIEW=0
    load_active_values
}

action_pick_timezone() {
    PICKER_TITLE="Select System Timezone"
    mapfile -t PICKER_ITEMS < <(timedatectl list-timezones 2>/dev/null)
    PICKER_HINTS=()
    local t
    for t in "${PICKER_ITEMS[@]}"; do
        PICKER_HINTS+=("${t%%/*}") # Hint is the region
    done
    PICKER_CALLBACK="picker_cb_timezone"
    PICKER_SELECTED=0
    PICKER_SCROLL=0

    PARENT_ROW=$SELECTED_ROW
    PARENT_SCROLL=$SCROLL_OFFSET
    CURRENT_VIEW=2
    clear_status
}

picker_cb_timezone() {
    local selected=$1
    if ! acquire_sudo; then return 1; fi
    set_status "Applying timezone: $selected..."
    if sudo timedatectl set-timezone "$selected"; then
        set_status "Timezone successfully set to $selected."
        LAST_WRITE_CHANGED=1
    else
        set_status "Failed to set timezone."
    fi
}

action_pick_locale() {
    PICKER_TITLE="Select System Locale"
    # Parse available locales from the authoritative arch supported list
    if [[ -f /usr/share/i18n/SUPPORTED ]]; then
        mapfile -t PICKER_ITEMS < <(grep -E '^[a-zA-Z]' /usr/share/i18n/SUPPORTED | awk '{print $1}')
    else
        PICKER_ITEMS=("en_US.UTF-8")
    fi
    PICKER_HINTS=()
    local i
    for i in "${PICKER_ITEMS[@]}"; do PICKER_HINTS+=("Requires generation"); done
    
    PICKER_CALLBACK="picker_cb_locale"
    PICKER_SELECTED=0
    PICKER_SCROLL=0

    PARENT_ROW=$SELECTED_ROW
    PARENT_SCROLL=$SCROLL_OFFSET
    CURRENT_VIEW=2
    clear_status
}

picker_cb_locale() {
    local selected=$1
    if ! acquire_sudo; then return 1; fi
    
    set_status "Uncommenting $selected in /etc/locale.gen..."
    # High-performance sed to uncomment the selected locale
    if ! sudo sed -i -E "s/^#[[:space:]]*(${selected}[[:space:]]+UTF-8.*)/\1/" /etc/locale.gen; then
        set_status "Failed to modify /etc/locale.gen"
        return 1
    fi
    
    set_status "Compiling locale $selected (may take a moment)..."
    # Restore TTY temporarily for locale-gen output visibility if desired, but for seamless TUI, keep it quiet
    if ! sudo locale-gen >/dev/null 2>&1; then
        set_status "locale-gen failed."
        return 1
    fi
    
    set_status "Setting system locale via localectl..."
    if sudo localectl set-locale "LANG=$selected"; then
        set_status "System locale successfully set to $selected."
        LAST_WRITE_CHANGED=1
    else
        set_status "Failed to set localectl."
    fi
}

action_pick_keymap() {
    PICKER_TITLE="Select TTY Keymap (vconsole)"
    mapfile -t PICKER_ITEMS < <(localectl list-x11-keymap-layouts 2>/dev/null || echo "us")
    PICKER_CALLBACK="picker_cb_keymap"
    PICKER_SELECTED=0
    PICKER_SCROLL=0

    PARENT_ROW=$SELECTED_ROW
    PARENT_SCROLL=$SCROLL_OFFSET
    CURRENT_VIEW=2
    clear_status
}

picker_cb_keymap() {
    local selected=$1
    if ! acquire_sudo; then return 1; fi
    set_status "Applying TTY keymap: $selected..."
    if sudo localectl set-keymap "$selected"; then
        set_status "Keymap successfully set."
        LAST_WRITE_CHANGED=1
    else
        set_status "Failed to set keymap."
    fi
}

post_write_action() {
    # Hook for reloading specific UI elements if needed in the future
    :
}

# =============================================================================
# CONSTANTS AND STATE
# =============================================================================

declare _h_line_buf
printf -v _h_line_buf '%*s' "$BOX_INNER_WIDTH" ''
declare -r H_LINE="${_h_line_buf// /─}"
unset _h_line_buf

# ANSI constants.
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
declare -r ALT_SCREEN_ON=$'\033[?1049h'
declare -r ALT_SCREEN_OFF=$'\033[?1049l'
declare -r MOUSE_ON=$'\033[?1000h\033[?1002h\033[?1006h'
declare -r MOUSE_OFF=$'\033[?1000l\033[?1002l\033[?1006l'

declare -r ESC_READ_TIMEOUT=0.08
declare -r READ_LOOP_TIMEOUT=0.25
declare -r UNSET_MARKER='«unset»'

declare -i SELECTED_ROW=0 CURRENT_TAB=0 SCROLL_OFFSET=0
declare -ri TAB_COUNT=${#TABS[@]}
declare -a TAB_ZONES=()
declare -i TAB_SCROLL_START=0
declare ORIGINAL_STTY=""
declare -i TUI_STARTED=0

declare -a TAB_SAVED_ROW=()
declare -a TAB_SAVED_SCROLL=()
for (( _ti = 0; _ti < TAB_COUNT; _ti++ )); do
    TAB_SAVED_ROW+=("0")
    TAB_SAVED_SCROLL+=("0")
done
unset _ti

declare -i CURRENT_VIEW=0
declare CURRENT_MENU_ID=""
declare -i PARENT_ROW=0 PARENT_SCROLL=0
declare -gi RESIZE_PENDING=0

declare PICKER_TITLE=""
declare -a PICKER_ITEMS=()
declare -a PICKER_HINTS=()
declare PICKER_CALLBACK=""
declare -i PICKER_SELECTED=0 PICKER_SCROLL=0

declare -i SUDO_AUTHENTICATED=0

declare -i TERM_ROWS=0 TERM_COLS=0
declare -ri MIN_TERM_COLS=$(( BOX_INNER_WIDTH + 2 ))
declare -ri MIN_TERM_ROWS=$(( HEADER_ROWS + MAX_DISPLAY_ROWS + 5 ))

declare -gi LAST_WRITE_CHANGED=0
declare STATUS_MESSAGE=""
declare LEFT_ARROW_ZONE=""
declare RIGHT_ARROW_ZONE=""

declare -A ITEM_MAP=()
declare -A VALUE_CACHE=()
declare -A DEFAULTS=()

for (( _ti = 0; _ti < TAB_COUNT; _ti++ )); do
    declare -ga "TAB_ITEMS_${_ti}=()"
done
unset _ti

# =============================================================================
# SYSTEM HELPERS
# =============================================================================

log_err() {
    printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$1" >&2
}

set_status() { declare -g STATUS_MESSAGE=$1; }
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

    if (( TUI_STARTED )) && [[ -t 1 ]]; then
        printf '\n' 2>/dev/null || :
    fi
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 131' QUIT
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

trim_spaces() {
    local v=$1
    v=${v#"${v%%[![:space:]]*}"}
    v=${v%"${v##*[![:space:]]}"}
    REPLY=$v
}

# =============================================================================
# REGISTRATION
# =============================================================================

register() {
    local -i tab_idx=$1
    local label=$2 config=$3 default_val=${4:-}
    local key type block min max step
    IFS='|' read -r key type block min max step <<< "$config"

    if (( tab_idx < 0 || tab_idx >= TAB_COUNT )); then
        log_err "Register Error: Tab index out of range for '$label': $tab_idx"
        exit 1
    fi

    if [[ -n ${ITEM_MAP["${tab_idx}::${label}"]+_} ]]; then
        log_err "Register Error: Duplicate label in tab $tab_idx: $label"
        exit 1
    fi

    ITEM_MAP["${tab_idx}::${label}"]=$config
    [[ -n $default_val ]] && DEFAULTS["${tab_idx}::${label}"]=$default_val

    local -n _reg_tab_ref="TAB_ITEMS_${tab_idx}"
    _reg_tab_ref+=("$label")

    if [[ $type == menu ]]; then
        declare -ga "SUBMENU_ITEMS_${key}=()"
    fi
}

# =============================================================================
# VALUE ENGINE (DBUS INTEGRATION)
# =============================================================================

load_active_values() {
    local REPLY_REF REPLY_CTX item key type value
    get_active_context
    local -n _lav_items_ref="$REPLY_REF"

    for item in "${_lav_items_ref[@]}"; do
        IFS='|' read -r key type _ _ _ _ <<< "${ITEM_MAP["${REPLY_CTX}::${item}"]}"
        
        # Pull live state from systemd dbus interfaces
        case $key in
            curr_date) value=$(date +'%Y-%m-%d') ;;
            curr_time) value=$(date +'%H:%M:%S %Z') ;;
            set_date|set_time) value="[Interactive Prompt]" ;;
            ntp) 
                value=$(timedatectl show -p NTP --value 2>/dev/null)
                [[ $value == yes ]] && value=true || value=false
                ;;
            rtc_local)
                value=$(timedatectl show -p LocalRTC --value 2>/dev/null)
                [[ $value == yes ]] && value=true || value=false
                ;;
            pick_timezone)
                value=$(timedatectl show -p Timezone --value 2>/dev/null)
                ;;
            pick_locale)
                value=$(localectl status 2>/dev/null | grep 'System Locale' | grep -o 'LANG=[^ ]*' | cut -d= -f2)
                [[ -z $value ]] && value="$UNSET_MARKER"
                ;;
            pick_keymap)
                value=$(localectl status 2>/dev/null | grep 'VC Keymap' | awk -F': ' '{print $2}')
                [[ -z $value ]] && value="$UNSET_MARKER"
                ;;
            sys_host) value=$(hostname 2>/dev/null || echo "Unknown") ;;
            sys_os) value=$(grep -m1 PRETTY_NAME /etc/os-release | cut -d '"' -f 2 2>/dev/null || echo "Arch Linux") ;;
            sys_kernel) value=$(uname -r 2>/dev/null || echo "Unknown") ;;
            sys_uptime) value=$(uptime -p 2>/dev/null | sed 's/up //' || echo "Unknown") ;;
            *) 
                value="$UNSET_MARKER" 
                ;;
        esac
        VALUE_CACHE["${REPLY_CTX}::${item}"]=$value
    done
}

modify_value() {
    local label=$1
    local -i direction=$2
    local REPLY_REF REPLY_CTX key type current new_val
    get_active_context
    local -n _items_ref="$REPLY_REF"
    IFS='|' read -r key type _ _ _ _ <<< "${ITEM_MAP["${REPLY_CTX}::${label}"]}"
    current=${VALUE_CACHE["${REPLY_CTX}::${label}"]:-}

    # Only booleans (NTP, RTC) are handled by arrow keys directly in this refactor.
    # Timezones and Locales use the explicit Action -> Picker flow.
    case $type in
        bool)
            [[ $current == true ]] && new_val=false || new_val=true
            ;;
        info|action|menu) return 0 ;;
        *) return 0 ;;
    esac

    # Handle systemd dbus commit
    if [[ $key == "ntp" ]]; then
        if ! acquire_sudo; then return 0; fi
        local sysd_val="false"
        [[ $new_val == true ]] && sysd_val="true"
        
        if sudo timedatectl set-ntp "$sysd_val"; then
            VALUE_CACHE["${REPLY_CTX}::${label}"]=$new_val
            clear_status
            LAST_WRITE_CHANGED=1
            post_write_action
        else
            set_status "Failed to modify NTP via timedatectl."
        fi
    elif [[ $key == "rtc_local" ]]; then
        if ! acquire_sudo; then return 0; fi
        local sysd_val="0"
        [[ $new_val == true ]] && sysd_val="1"
        
        if sudo timedatectl set-local-rtc "$sysd_val"; then
            VALUE_CACHE["${REPLY_CTX}::${label}"]=$new_val
            clear_status
            LAST_WRITE_CHANGED=1
            post_write_action
        else
            set_status "Failed to modify Local RTC."
        fi
    fi
    return 0
}

# =============================================================================
# LINE INPUT AND SUDO
# =============================================================================

acquire_sudo() {
    if sudo -n true 2>/dev/null; then
        SUDO_AUTHENTICATED=1
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

    stty -icanon -echo -ixon min 0 time 0 < /dev/tty 2>/dev/null || :
    printf '%s%s%s%s' "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"

    if (( result == 0 )); then
        SUDO_AUTHENTICATED=1
        set_status "Authentication successful."
        return 0
    fi
    set_status "Authentication failed or cancelled."
    return 1
}

# =============================================================================
# RENDERING
# =============================================================================

compute_scroll_window() {
    local -i count=$1
    if (( count == 0 )); then
        SELECTED_ROW=0; SCROLL_OFFSET=0; _vis_start=0; _vis_end=0; return 0
    fi
    (( SELECTED_ROW < 0 )) && SELECTED_ROW=0
    (( SELECTED_ROW >= count )) && SELECTED_ROW=$(( count - 1 ))
    (( SELECTED_ROW < SCROLL_OFFSET )) && SCROLL_OFFSET=$SELECTED_ROW
    (( SELECTED_ROW >= SCROLL_OFFSET + MAX_DISPLAY_ROWS )) && SCROLL_OFFSET=$(( SELECTED_ROW - MAX_DISPLAY_ROWS + 1 ))
    local -i max_scroll=$(( count - MAX_DISPLAY_ROWS ))
    (( max_scroll < 0 )) && max_scroll=0
    (( SCROLL_OFFSET > max_scroll )) && SCROLL_OFFSET=$max_scroll
    _vis_start=$SCROLL_OFFSET
    _vis_end=$(( SCROLL_OFFSET + MAX_DISPLAY_ROWS ))
    (( _vis_end > count )) && _vis_end=$count
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
    local item val display type config padded_item max_len

    for (( ri = vs; ri < ve; ri++ )); do
        item=${_items[ri]}
        val=${VALUE_CACHE["${ctx}::${item}"]:-$UNSET_MARKER}
        config=${ITEM_MAP["${ctx}::${item}"]}
        IFS='|' read -r _ type _ _ _ _ <<< "$config"
        case $type in
            menu) display="${C_YELLOW}[+] Open Menu ...${C_RESET}" ;;
            action) display="${C_GREEN}▶ [${val}]${C_RESET}" ;;
            info) display="${C_WHITE}${val}${C_RESET}" ;;
            *)
                case $val in
                    true) display="${C_GREEN}ON${C_RESET}" ;;
                    false) display="${C_RED}OFF${C_RESET}" ;;
                    "$UNSET_MARKER") display="${C_YELLOW}⚠ UNSET${C_RESET}" ;;
                    *) display="${C_WHITE}${val}${C_RESET}" ;;
                esac
                ;;
        esac
        max_len=$(( ITEM_PADDING - 1 ))
        if (( ${#item} > ITEM_PADDING )); then
            printf -v padded_item "%-${max_len}s…" "${item:0:max_len}"
        else
            printf -v padded_item "%-${ITEM_PADDING}s" "$item"
        fi
        if (( ri == SELECTED_ROW )); then
            _buf+="${C_CYAN} ➤ ${C_INVERSE}${padded_item}${C_RESET} : ${display}${CLR_EOL}"$'\n'
        else
            _buf+="    ${padded_item} : ${display}${CLR_EOL}"$'\n'
        fi
    done

    local -i rows_rendered=$(( ve - vs ))
    for (( ri = rows_rendered; ri < MAX_DISPLAY_ROWS; ri++ )); do _buf+="${CLR_EOL}"$'\n'; done
}

draw_main_view() {
    local buf="" pad_buf="" tab_line name display_name item_var
    local -i i current_col=3 zone_start count left_pad right_pad vis_len _vis_start _vis_end

    buf+="${CURSOR_HOME}${C_MAGENTA}┌${H_LINE}┐${C_RESET}${CLR_EOL}"$'\n'
    strip_ansi "$APP_TITLE"; local -i t_len=${#REPLY}
    strip_ansi "$APP_VERSION"; local -i v_len=${#REPLY}
    vis_len=$(( t_len + v_len + 1 ))
    left_pad=$(( (BOX_INNER_WIDTH - vis_len) / 2 )); (( left_pad < 0 )) && left_pad=0
    right_pad=$(( BOX_INNER_WIDTH - vis_len - left_pad )); (( right_pad < 0 )) && right_pad=0
    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad_buf}${C_WHITE}${APP_TITLE} ${C_CYAN}${APP_VERSION}${C_MAGENTA}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}│${C_RESET}${CLR_EOL}"$'\n'

    (( TAB_SCROLL_START > CURRENT_TAB )) && TAB_SCROLL_START=$CURRENT_TAB
    (( TAB_SCROLL_START < 0 )) && TAB_SCROLL_START=0
    local -i max_tab_width=$(( BOX_INNER_WIDTH - 6 ))
    LEFT_ARROW_ZONE=""; RIGHT_ARROW_ZONE=""

    while true; do
        tab_line="${C_MAGENTA}│ "
        current_col=3
        TAB_ZONES=()
        local -i used_len=0
        if (( TAB_SCROLL_START > 0 )); then
            tab_line+="${C_YELLOW}«${C_RESET} "
            LEFT_ARROW_ZONE="$current_col:$(( current_col + 1 ))"
        else
            tab_line+="  "
        fi
        used_len=$(( used_len + 2 )); current_col=$(( current_col + 2 ))

        for (( i = TAB_SCROLL_START; i < TAB_COUNT; i++ )); do
            name=${TABS[i]}; display_name=$name
            local -i tab_name_len=${#name}
            local -i chunk_len=$(( tab_name_len + 4 ))
            local -i reserve=0
            if (( i < TAB_COUNT - 1 )); then reserve=2; fi
            if (( used_len + chunk_len + reserve > max_tab_width )); then
                if (( i < CURRENT_TAB || (i == CURRENT_TAB && TAB_SCROLL_START < CURRENT_TAB) )); then
                    TAB_SCROLL_START=$(( TAB_SCROLL_START + 1 )); continue 2
                fi
                if (( i == CURRENT_TAB )); then
                    local -i avail_label=$(( max_tab_width - used_len - reserve - 4 ))
                    (( avail_label < 1 )) && avail_label=1
                    if (( tab_name_len > avail_label )); then
                        if (( avail_label == 1 )); then display_name="…"; else display_name="${name:0:avail_label-1}…"; fi
                        tab_name_len=${#display_name}; chunk_len=$(( tab_name_len + 4 ))
                    fi
                    zone_start=$current_col
                    tab_line+="${C_CYAN}${C_INVERSE} ${display_name} ${C_RESET}${C_MAGENTA}│ "
                    TAB_ZONES+=("${zone_start}:$(( zone_start + tab_name_len + 1 ))")
                    used_len=$(( used_len + chunk_len )); current_col=$(( current_col + chunk_len ))
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
            if (( i == CURRENT_TAB )); then tab_line+="${C_CYAN}${C_INVERSE} ${display_name} ${C_RESET}${C_MAGENTA}│ "; else tab_line+="${C_GREY} ${display_name} ${C_MAGENTA}│ "; fi
            TAB_ZONES+=("${zone_start}:$(( zone_start + tab_name_len + 1 ))")
            used_len=$(( used_len + chunk_len )); current_col=$(( current_col + chunk_len ))
        done
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

    buf+=$'\n'"${C_CYAN} [Tab] Category  [←/→ h/l] Adjust Toggle  [Enter] Action  [q] Quit${C_RESET}${CLR_EOL}"$'\n'
    if [[ -n $STATUS_MESSAGE ]]; then buf+="${C_CYAN} Status: ${C_RED}${STATUS_MESSAGE}${C_RESET}${CLR_EOL}${CLR_EOS}"; else buf+="${C_CYAN} Engine: ${C_WHITE}systemd (timedatectl/localectl)${C_RESET}${CLR_EOL}${CLR_EOS}"; fi
    printf '%s' "$buf"
}

draw_picker_view() {
    local buf="" pad_buf="" title sub breadcrumb item hint padded hint_trim
    local -i left_pad right_pad vis_len pad_needed count i vstart vend rows_rendered max_len
    buf+="${CURSOR_HOME}${C_MAGENTA}┌${H_LINE}┐${C_RESET}${CLR_EOL}"$'\n'
    title=" PICKER "; sub=" ${PICKER_TITLE} "
    strip_ansi "$title"; local -i t_len=${#REPLY}; strip_ansi "$sub"; local -i s_len=${#REPLY}
    vis_len=$(( t_len + s_len )); left_pad=$(( (BOX_INNER_WIDTH - vis_len) / 2 )); (( left_pad < 0 )) && left_pad=0
    right_pad=$(( BOX_INNER_WIDTH - vis_len - left_pad )); (( right_pad < 0 )) && right_pad=0
    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad_buf}${C_YELLOW}${title}${C_GREY}${sub}${C_MAGENTA}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}│${C_RESET}${CLR_EOL}"$'\n'
    breadcrumb=" « Esc to cancel"; strip_ansi "$breadcrumb"; local -i b_len=${#REPLY}; pad_needed=$(( BOX_INNER_WIDTH - b_len )); (( pad_needed < 0 )) && pad_needed=0
    printf -v pad_buf '%*s' "$pad_needed" ''
    buf+="${C_MAGENTA}│${C_CYAN}${breadcrumb}${C_RESET}${pad_buf}${C_MAGENTA}│${C_RESET}${CLR_EOL}"$'\n'
    buf+="${C_MAGENTA}└${H_LINE}┘${C_RESET}${CLR_EOL}"$'\n'

    count=${#PICKER_ITEMS[@]}
    if (( count == 0 )); then
        PICKER_SELECTED=0; PICKER_SCROLL=0
    else
        (( PICKER_SELECTED < 0 )) && PICKER_SELECTED=0
        (( PICKER_SELECTED >= count )) && PICKER_SELECTED=$(( count - 1 ))
        (( PICKER_SELECTED < PICKER_SCROLL )) && PICKER_SCROLL=$PICKER_SELECTED
        (( PICKER_SELECTED >= PICKER_SCROLL + MAX_DISPLAY_ROWS )) && PICKER_SCROLL=$(( PICKER_SELECTED - MAX_DISPLAY_ROWS + 1 ))
        local -i max_scroll=$(( count - MAX_DISPLAY_ROWS )); (( max_scroll < 0 )) && max_scroll=0; (( PICKER_SCROLL > max_scroll )) && PICKER_SCROLL=$max_scroll
    fi
    vstart=$PICKER_SCROLL; vend=$(( PICKER_SCROLL + MAX_DISPLAY_ROWS )); (( vend > count )) && vend=$count
    (( PICKER_SCROLL > 0 )) && buf+="${C_GREY}    ▲ (more above)${CLR_EOL}${C_RESET}"$'\n' || buf+="${CLR_EOL}"$'\n'
    max_len=$(( ITEM_PADDING - 1 ))
    for (( i = vstart; i < vend; i++ )); do
        item=${PICKER_ITEMS[i]}; hint=${PICKER_HINTS[i]:-}
        if (( ${#item} > ITEM_PADDING )); then printf -v padded "%-${max_len}s…" "${item:0:max_len}"; else printf -v padded "%-${ITEM_PADDING}s" "$item"; fi
        hint_trim=$hint; (( ${#hint_trim} > 32 )) && hint_trim="${hint_trim:0:31}…"
        if (( i == PICKER_SELECTED )); then buf+="${C_CYAN} ➤ ${C_INVERSE}${padded}${C_RESET} ${C_GREY}${hint_trim}${C_RESET}${CLR_EOL}"$'\n'; else buf+="    ${padded} ${C_GREY}${hint_trim}${C_RESET}${CLR_EOL}"$'\n'; fi
    done
    rows_rendered=$(( vend - vstart )); for (( i = rows_rendered; i < MAX_DISPLAY_ROWS; i++ )); do buf+="${CLR_EOL}"$'\n'; done
    if (( count > MAX_DISPLAY_ROWS )); then
        local pos_info="[$(( PICKER_SELECTED + 1 ))/${count}]"
        (( vend < count )) && buf+="${C_GREY}    ▼ (more below) ${pos_info}${CLR_EOL}${C_RESET}"$'\n' || buf+="${C_GREY}                   ${pos_info}${CLR_EOL}${C_RESET}"$'\n'
    else
        buf+="${CLR_EOL}"$'\n'
    fi
    buf+=$'\n'"${C_CYAN} [↑/↓ j/k] Navigate  [Enter] Select  [Esc] Cancel  [q] Quit${C_RESET}${CLR_EOL}"$'\n'
    if [[ -n $STATUS_MESSAGE ]]; then buf+="${C_CYAN} Status: ${C_RED}${STATUS_MESSAGE}${C_RESET}${CLR_EOL}${CLR_EOS}"; elif (( count == 0 )); then buf+="${C_CYAN} ${C_YELLOW}(no items - press Esc to go back)${C_RESET}${CLR_EOL}${CLR_EOS}"; else buf+="${C_CYAN} ${count} item(s)${C_RESET}${CLR_EOL}${CLR_EOS}"; fi
    printf '%s' "$buf"
}

draw_ui() {
    update_terminal_size
    if ! terminal_size_ok; then draw_small_terminal_notice; return; fi
    case $CURRENT_VIEW in
        0) draw_main_view ;;
        2) draw_picker_view ;;
    esac
}

# =============================================================================
# NAVIGATION AND INPUT
# =============================================================================

exit_picker() {
    CURRENT_VIEW=0
    SELECTED_ROW=$PARENT_ROW
    SCROLL_OFFSET=$PARENT_SCROLL
    PICKER_ITEMS=(); PICKER_HINTS=(); PICKER_TITLE=""; PICKER_CALLBACK=""
    load_active_values
}

picker_navigate() {
    local -i dir=$1 count=${#PICKER_ITEMS[@]}
    (( count == 0 )) && return 0
    PICKER_SELECTED=$(( (PICKER_SELECTED + dir + count) % count ))
}

picker_confirm() {
    local -i count=${#PICKER_ITEMS[@]}
    (( count == 0 )) && { exit_picker; return; }
    local chosen=${PICKER_ITEMS[PICKER_SELECTED]} cb=$PICKER_CALLBACK
    exit_picker
    [[ -n $cb && $(type -t "$cb") == function ]] && "$cb" "$chosen"
    load_active_values
}

navigate() {
    local -i dir=$1 count
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _nav_items_ref="$REPLY_REF"
    count=${#_nav_items_ref[@]}
    (( count == 0 )) && return 0
    SELECTED_ROW=$(( (SELECTED_ROW + dir + count) % count ))
    clear_status
}

navigate_page() {
    local -i dir=$1 count
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _items_ref="$REPLY_REF"
    count=${#_items_ref[@]}
    (( count == 0 )) && return 0
    SELECTED_ROW=$(( SELECTED_ROW + dir * MAX_DISPLAY_ROWS ))
    (( SELECTED_ROW < 0 )) && SELECTED_ROW=0
    (( SELECTED_ROW >= count )) && SELECTED_ROW=$(( count - 1 ))
    clear_status
}

navigate_end() {
    local -i target=$1 count
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _items_ref="$REPLY_REF"
    count=${#_items_ref[@]}
    (( count == 0 )) && return 0
    (( target == 0 )) && SELECTED_ROW=0 || SELECTED_ROW=$(( count - 1 ))
    clear_status
}

adjust() {
    local -i dir=$1
    local REPLY_REF REPLY_CTX label type
    get_active_context
    local -n _items_ref="$REPLY_REF"
    (( ${#_items_ref[@]} == 0 )) && return 0
    label=${_items_ref[SELECTED_ROW]}
    IFS='|' read -r _ type _ _ _ _ <<< "${ITEM_MAP["${REPLY_CTX}::${label}"]}"
    [[ $type == action || $type == info ]] && return 0
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
    local REPLY_REF REPLY_CTX item config key type
    get_active_context
    local -n _act_ref="$REPLY_REF"
    (( ${#_act_ref[@]} == 0 )) && return 1
    item=${_act_ref[SELECTED_ROW]}
    config=${ITEM_MAP["${REPLY_CTX}::${item}"]}
    IFS='|' read -r key type _ _ _ _ <<< "$config"
    case $type in
        action)
            if [[ $(type -t "action_${key}") == function ]]; then
                "action_${key}"
            else
                set_status "No handler defined for action: $key"
            fi
            return 0
            ;;
        info)
            return 1
            ;;
    esac
    return 1
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

            for (( i = 0; i < ${#TAB_ZONES[@]}; i++ )); do
                zone="${TAB_ZONES[i]}"
                start="${zone%%:*}"; end="${zone##*:}"
                if (( x >= start && x <= end )); then set_tab "$(( i + TAB_SCROLL_START ))"; return 0; fi
            done
        fi
        return 0
    fi

    local -i effective_start=$(( ITEM_START_ROW + 1 ))
    if (( y >= effective_start && y < effective_start + MAX_DISPLAY_ROWS )); then
        local -i clicked_idx=$(( y - effective_start + SCROLL_OFFSET ))

        local _target_var_name
        if (( CURRENT_VIEW == 0 )); then
            _target_var_name="TAB_ITEMS_${CURRENT_TAB}"
        fi

        local -n _mouse_items_ref="$_target_var_name"
        local -i count=${#_mouse_items_ref[@]}

        if (( clicked_idx >= 0 && clicked_idx < count )); then
            SELECTED_ROW=$clicked_idx
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
    button=$field1; x=$field2; y=$field3

    if (( button == 64 )); then picker_navigate -1; return 0; fi
    if (( button == 65 )); then picker_navigate 1; return 0; fi

    if [[ "$terminator" != "M" ]]; then return 0; fi

    local -i effective_start=$(( ITEM_START_ROW + 1 ))
    if (( y >= effective_start && y < effective_start + MAX_DISPLAY_ROWS )); then
        local -i clicked_idx=$(( y - effective_start + PICKER_SCROLL ))
        local -i count=${#PICKER_ITEMS[@]}
        if (( clicked_idx >= 0 && clicked_idx < count )); then
            PICKER_SELECTED=$clicked_idx
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
        while IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char < /dev/tty; do
            _esc_out+=$char
            [[ $char =~ [a-zA-Z~] ]] && break
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
        '[5~') picker_navigate -$MAX_DISPLAY_ROWS; return ;;
        '[6~') picker_navigate $MAX_DISPLAY_ROWS; return ;;
        '[H'|'[1~') PICKER_SELECTED=0; return ;;
        '[F'|'[4~') PICKER_SELECTED=$(( ${#PICKER_ITEMS[@]} - 1 )); return ;;
        '['*'<'*[Mm]) handle_mouse_picker "$key"; return ;;
    esac
    case $key in
        ESC) exit_picker ;;
        k|K) picker_navigate -1 ;;
        j|J) picker_navigate 1 ;;
        $'\x15') picker_navigate -$MAX_DISPLAY_ROWS ;; # Ctrl+U
        $'\x04') picker_navigate $MAX_DISPLAY_ROWS ;;  # Ctrl+D
        g) PICKER_SELECTED=0 ;;
        G) PICKER_SELECTED=$(( ${#PICKER_ITEMS[@]} - 1 )) ;;
        ''|$'\n') picker_confirm ;;
        q|Q|$'\x03') exit 0 ;;
    esac
}

handle_input_router() {
    local key=$1 escape_seq=""
    if [[ $key == $'\x1b' ]]; then
        if read_escape_seq escape_seq; then
            key=$escape_seq
            [[ $key == "" || $key == $'\n' ]] && key=$'\e\n'
        else
            key=ESC
        fi
    fi
    if ! terminal_size_ok; then
        case $key in q|Q|$'\x03') exit 0 ;; esac
        return 0
    fi
    case $CURRENT_VIEW in
        0) handle_key_main "$key" ;;
        2) handle_key_picker "$key" ;;
    esac
}

# =============================================================================
# ENTRYPOINT
# =============================================================================

main() {
    if (( BASH_VERSINFO[0] < 5 )); then log_err "Bash 5.0+ required"; exit 1; fi
    if [[ ! -t 0 || ! -t 1 ]]; then log_err "Interactive TTY stdin/stdout required"; exit 1; fi

    local dep
    for dep in stty sudo timedatectl localectl awk grep sed locale-gen; do
        command -v "$dep" >/dev/null 2>&1 || { log_err "Missing dependency: $dep"; exit 1; }
    done

    register_items

    ORIGINAL_STTY=$(stty -g < /dev/tty 2>/dev/null) || ORIGINAL_STTY=""
    if [[ -z $ORIGINAL_STTY ]]; then log_err "Failed to read terminal settings. A controlling TTY is required."; exit 1; fi
    stty -icanon -echo -ixon min 0 time 0 < /dev/tty 2>/dev/null || { log_err "Failed to configure terminal raw input."; exit 1; }

    TUI_STARTED=1
    printf '%s%s%s%s%s' "$ALT_SCREEN_ON" "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"
    
    load_active_values || true
    trap 'RESIZE_PENDING=1' WINCH

    local key
    while true; do
        draw_ui || true
        
        if IFS= read -rsn1 -t "$READ_LOOP_TIMEOUT" key < /dev/tty; then
            if (( RESIZE_PENDING )); then RESIZE_PENDING=0; fi
            handle_input_router "$key" || true
        else
            if (( RESIZE_PENDING )); then RESIZE_PENDING=0; fi
            # Auto-refresh values every cycle to reflect changes
            if (( CURRENT_VIEW == 0 )); then load_active_values || true; fi 
        fi
    done
}

main "$@"
