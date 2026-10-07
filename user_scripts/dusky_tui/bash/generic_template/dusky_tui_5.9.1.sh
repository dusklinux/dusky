#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Dusky TUI Engine - Generic Configuration Template v5.9.1
# Target: Linux flat or sectioned key/value configuration files
# 
# AUDIT UPDATE (2026-10-01):
#   Literal-safe config writes, fresh-cache no-ops, single-stage atomic saves,
#   conflict detection, cancellable input, bounded escape/mouse parsing,
#   event-driven redraws, and regression tests in tests/test_template.py.
# Config grammar: global / [section], key=value or key value, whole-line # / ;
# comments, optional outer quotes, no inline-comment or escape interpretation.
# Atomic replacement preserves owner/group/mode; hard links, ACLs and xattrs
# require an application-specific writer. No power-loss durability guarantee.
# -----------------------------------------------------------------------------

set -Eeuo pipefail
shopt -s extglob

# =============================================================================
# USER CONFIGURATION
# =============================================================================

: "${XDG_CONFIG_HOME:=${HOME}/.config}"
declare CONFIG_FILE="${DUSKY_CONFIG_FILE:-${XDG_CONFIG_HOME}/myapp/settings.conf}"
declare -r APP_TITLE="Generic System Config Editor"
declare -r APP_VERSION="v5.9.1"

# Dimensions & layout.
declare -ri MAX_DISPLAY_ROWS=14
declare -ri BOX_INNER_WIDTH=76
declare -ri ADJUST_THRESHOLD=38
declare -ri ITEM_PADDING=32

declare -ri HEADER_ROWS=4
declare -ri TAB_ROW=3
declare -ri ITEM_START_ROW=$(( HEADER_ROWS + 1 ))

declare -ra TABS=("General" "Network" "Display" "System")

register_items() {
    # Generic Config Layout: register tab_idx "Label" 'key|type|scope|min|max|step' "default"
    # Note: 'scope' corresponds to [Section] in INI files, leave blank for global scope.
    # Omit the default argument to make Reset remove a setting; "" is an empty value.
    # int: decimal integers up to 18 digits, step defaults to 1.
    # float: finite decimal/exponent values, step defaults to 0.1.
    # cycle: comma-separated options in the min field; bool: true/false/yes/no/on/off/1/0.
    # action: define action_KEY(); menu: register before its register_child() entries.
    # Menus have one level. Reset All applies to the current tab or submenu only.
    register 0 "Enable Service"   'service_enabled|bool||||'              "true"
    register 0 "Timeout (ms)"     'timeout|int||0|1000|50'                "100"
    register 0 "Log Prefix"       'log_prefix|string||||'                 "myapp_"

    register 1 "Hostname"         'hostname|action||||'                   ""
    register 1 "Protocol"         'protocol|cycle|network|tcp,udp,icmp||' "tcp"
    
    register 2 "Border Size"      'border_size|int|display|0|10|1'        "2"
    register 2 "Blur Enabled"     'blur_enabled|bool|display|||'          "true"

    register 3 "Advanced Settings" 'advanced_settings|menu||||'           ""
    register_child "advanced_settings" "Allow Root Login" 'PermitRootLogin|bool|security|||' "false"
    register_child "advanced_settings" "Max Retries"      'MaxAuthTries|int|security|1|10|1' "3"

    register 3 "Shadow Color"     'color|cycle|decoration|0xee1a1a1a,0xff000000||' "0xee1a1a1a"

    register 3 "Custom Path"      'demo_text|action||||' ""
    register 3 "Select Theme"     'demo_picker|action||||' ""
    register 3 "Restart Daemon"   'demo_sudo|action||||' ""
}

action_hostname() {
    local user_input=""
    prompt_line_input "Enter new hostname:" user_input || return 0
    if [[ -n $user_input ]]; then
        set_status "Hostname set to: $user_input"
    else
        clear_status
    fi
}

action_demo_text() {
    local user_input=""
    prompt_line_input "Enter a custom file path:" user_input || return 0
    if [[ -n $user_input ]]; then
        set_status "You typed: $user_input"
    else
        clear_status
    fi
}

action_demo_picker() {
    PICKER_TITLE="Select a Workspace Theme"
    PICKER_ITEMS=("Catppuccin Mocha" "Nord" "Dracula" "Gruvbox" "Tokyo Night")
    PICKER_HINTS=("Warm & Pastel" "Arctic Cold" "Vampire Dark" "Retro Groove" "Neon Lights")
    PICKER_CALLBACK="picker_cb_demo_theme"
    PICKER_SELECTED=0
    PICKER_SCROLL=0

    PICKER_PARENT_VIEW=$CURRENT_VIEW
    PICKER_PARENT_ROW=$SELECTED_ROW
    PICKER_PARENT_SCROLL=$SCROLL_OFFSET
    CURRENT_VIEW=2
    clear_status
}

picker_cb_demo_theme() {
    local selected=$1
    set_status "Selected Theme: $selected"
}

action_demo_sudo() {
    acquire_sudo || return 0
    set_status "Sudo acquired. Service restart simulated."
}

post_write_action() {
    # Called after changed saves, once after Reset All. Report hook failures here.
    # systemctl reload my-daemon.service >/dev/null 2>&1 || set_status "Reload failed."
    :
}

# =============================================================================
# CONSTANTS AND STATE
# =============================================================================

declare _h_line_buf
printf -v _h_line_buf '%*s' "$BOX_INNER_WIDTH" '' || true
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
# Basic clicks first, then button-motion only (1002), never all-motion (1003).
# Unsupported 1002 leaves basic clicks working; SGR motion code 32 means left held.
declare -r MOUSE_ON=$'\033[?1000h\033[?1002h\033[?1006h\033[?2004h'
declare -r MOUSE_OFF=$'\033[?1000l\033[?1002l\033[?1006l\033[?2004l'

declare -r ESC_READ_TIMEOUT=0.08
declare -r READ_LOOP_TIMEOUT=0.25
declare -ri MAX_ESCAPE_BYTES=64
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
declare -i PICKER_PARENT_VIEW=0 PICKER_PARENT_ROW=0 PICKER_PARENT_SCROLL=0
declare -gi RESIZE_PENDING=0 PASTE_ACTIVE=0
declare -gi MOUSE_CLICK_PENDING=0 MOUSE_PRESS_X=0 MOUSE_PRESS_Y=0
declare MOUSE_PRESS_CONTEXT=""
declare PASTE_TAIL=""

declare PICKER_TITLE=""
declare -a PICKER_ITEMS=()
declare -a PICKER_HINTS=()
declare PICKER_CALLBACK=""
declare -i PICKER_SELECTED=0 PICKER_SCROLL=0

declare _TMPFILE=""
declare _TMPMODE=""
declare -a _TEMP_PATHS=()
declare WRITE_TARGET=""
declare LOCK_TARGET=""

declare -i TERM_ROWS=0 TERM_COLS=0
declare -ri MIN_TERM_COLS=$(( BOX_INNER_WIDTH + 2 ))
declare -ri MIN_TERM_ROWS=$(( HEADER_ROWS + MAX_DISPLAY_ROWS + 6 ))

declare -gi LAST_WRITE_CHANGED=0
declare STATUS_MESSAGE=""
declare LEFT_ARROW_ZONE=""
declare RIGHT_ARROW_ZONE=""

declare -A ITEM_MAP=()
declare -A VALUE_CACHE=()
declare -A CONFIG_CACHE=()
declare CONFIG_SIGNATURE=""
declare -A DEFAULTS=()

for (( _ti = 0; _ti < TAB_COUNT; _ti++ )); do
    declare -ga "TAB_ITEMS_${_ti}=()"
done
unset _ti

# =============================================================================
# SYSTEM HELPERS
# =============================================================================

log_err() {
    printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$1" >&2 || true
}

set_status() { declare -g STATUS_MESSAGE=${1//[[:cntrl:]]/?}; }
clear_status() { declare -g STATUS_MESSAGE=""; }

register_temp() {
    local path=$1
    [[ -n $path ]] && _TEMP_PATHS+=("$path")
}

forget_temp() {
    local path=$1 kept=() item
    for item in "${_TEMP_PATHS[@]}"; do
        [[ $item == "$path" ]] || kept+=("$item")
    done
    _TEMP_PATHS=("${kept[@]}")
}

remove_temp() {
    local path=$1
    [[ -n $path && -e $path ]] && rm -f -- "$path" 2>/dev/null || :
    forget_temp "$path"
}

cleanup() {
    local path
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

    for path in "${_TEMP_PATHS[@]}"; do
        [[ -n $path && -e $path ]] && rm -f -- "$path" 2>/dev/null || :
    done
    
    _TEMP_PATHS=()
    _TMPFILE=""
    _TMPMODE=""
    if (( TUI_STARTED )) && [[ -t 1 ]]; then
        printf '\n' 2>/dev/null || :
    fi
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 131' QUIT
trap 'exit 143' TERM

path_dirname() {
    local path=$1
    if [[ $path == */* ]]; then
        REPLY=${path%/*}
        [[ -n $REPLY ]] || REPLY=/
    else
        REPLY=.
    fi
}

file_signature() {
    local path=$1
    LC_ALL=C stat -Lc '%d:%i:%s:%y:%z:%a:%u:%g' -- "$path"
}

release_lock_fd() {
    local fd=${1:-}
    if [[ $fd =~ ^[0-9]+$ ]]; then
        flock -u "$fd" 2>/dev/null || :
        exec {fd}>&- 2>/dev/null || :
    fi
}

resolve_write_target() {
    [[ -n $CONFIG_FILE ]] || { log_err "Config path is empty."; return 1; }
    path_dirname "$CONFIG_FILE"
    mkdir -p -- "$REPLY" || return 1
    # Opening an existing config must not change its timestamp.
    if [[ ! -e $CONFIG_FILE ]]; then
        ( set -o noclobber; : > "$CONFIG_FILE" ) || return 1
    fi
    WRITE_TARGET=$(realpath -e -- "$CONFIG_FILE") || return 1
    [[ -f $WRITE_TARGET && -r $WRITE_TARGET ]] || {
        log_err "Config must be a readable regular file."; return 1;
    }
    local lock_dir="${XDG_RUNTIME_DIR:-/tmp}/dusky_tui_locks_${UID}" digest
    mkdir -p -- "$lock_dir" || return 1
    digest=$(printf '%s' "$WRITE_TARGET" | sha256sum) || return 1
    LOCK_TARGET="${lock_dir}/${digest%% *}.lock"
}

create_tmpfile_for_target() {
    local target=$1 target_dir
    if [[ -n ${_TMPFILE:-} ]]; then
        remove_temp "$_TMPFILE"
    fi
    _TMPFILE=""
    _TMPMODE=""

    path_dirname "$target"; target_dir=$REPLY

    if ! _TMPFILE=$(mktemp --tmpdir="$target_dir" ".dusky.tmp.XXXXXXXXXX" 2>/dev/null); then
        _TMPFILE=""
        _TMPMODE=""
        return 1
    fi
    _TMPMODE="atomic"
    register_temp "$_TMPFILE"
    return 0
}

commit_tmpfile_to_target() {
    local target=$1
    [[ -n ${_TMPFILE:-} && -f $_TMPFILE && ${_TMPMODE:-} == atomic ]] || return 1
    [[ -e $target && -f $target ]] || return 1

    chown --reference="$target" -- "$_TMPFILE" 2>/dev/null || return 1
    chmod --reference="$target" -- "$_TMPFILE" 2>/dev/null || return 1
    mv -fT --no-copy -- "$_TMPFILE" "$target" || return 1

    forget_temp "$_TMPFILE"
    _TMPFILE=""
    _TMPMODE=""
    return 0
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

trim_spaces() {
    local v=$1
    v=${v#"${v%%[![:space:]]*}"}
    v=${v%"${v##*[![:space:]]}"}
    REPLY=$v
}

normalize_target() {
    local key=$1 scope=$2
    TARGET_KEY=$key
    TARGET_SCOPE=$scope
}

# =============================================================================
# REGISTRATION
# =============================================================================

is_int_literal() {
    [[ $1 =~ ^-?[0-9]{1,18}$ ]]
}

is_float_literal() {
    [[ $1 =~ ^-?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?$ ]] || return 1
    LC_ALL=C awk -v v="$1" 'BEGIN { exit (sprintf("%g", v + 0) ~ /inf|nan/) }'
}

number_le() {
    local left=$1 right=$2
    # AWK floating-point comparisons lose adjacent large integers.
    if is_int_literal "$left" && is_int_literal "$right"; then
        local l=$(( 10#${left#-} )) r=$(( 10#${right#-} ))
        [[ $left == -* ]] && l=$(( -l ))
        [[ $right == -* ]] && r=$(( -r ))
        (( l <= r ))
        return
    fi
    LC_ALL=C awk -v l="$left" -v r="$right" 'BEGIN { exit (l <= r ? 0 : 1) }'
}

validate_cycle_options() {
    local label=$1 options=$2 opt
    local -a opts=()
    IFS=',' read -r -a opts <<< "$options"
    if (( ${#opts[@]} == 0 )) || [[ $options == *, ]]; then
        log_err "Register Error: Cycle '$label' has no options."
        exit 1
    fi
    for opt in "${opts[@]}"; do
        trim_spaces "$opt"; opt=$REPLY
        if [[ -z $opt || $opt == *$'\n'* || $opt == *'|'* || $opt == *,* ]]; then
            log_err "Register Error: Cycle '$label' contains unsafe option: '$opt'"
            exit 1
        fi
    done
}

validate_item_config() {
    local label=$1 key=$2 type=$3 block=$4 min=${5:-} max=${6:-} step=${7:-}
    if [[ -z $label || $label == *$'\n'* ]]; then
        log_err "Register Error: Invalid label."
        exit 1
    fi
    if [[ -z $key || $key == *$'\n'* || $key == *[[:space:]=\|]* || $key == */* || $key == [\#\;\[]* ]]; then
        log_err "Register Error: Invalid key for '$label'."
        exit 1
    fi
    case $type in
        bool|int|float|cycle|menu|action|string) ;;
        *) log_err "Invalid type for '$label': $type"; exit 1 ;;
    esac
    
    trim_spaces "$block"
    if [[ $block != "$REPLY" ]]; then
        log_err "Register Error: Scope must not have outer whitespace for '$label'."; exit 1
    fi
    if [[ $block == *[$'\n\r'\[\]\|]* ]]; then
        log_err "Register Error: Invalid section for '$label': $block"
        exit 1
    fi
    
    case $type in
        int)
            if [[ -n $min ]] && ! is_int_literal "$min"; then log_err "Register Error: Invalid int min for '$label'."; exit 1; fi
            if [[ -n $max ]] && ! is_int_literal "$max"; then log_err "Register Error: Invalid int max for '$label'."; exit 1; fi
            if [[ -n $step ]]; then
                if ! is_int_literal "$step" || [[ $step == -* || ! $step =~ [1-9] ]]; then
                    log_err "Register Error: Invalid int step for '$label'."
                    exit 1
                fi
            fi
            if [[ -n $min && -n $max ]] && ! number_le "$min" "$max"; then
                log_err "Register Error: min > max for '$label'."
                exit 1
            fi
            ;;
        float)
            if [[ -n $min ]] && ! is_float_literal "$min"; then log_err "Register Error: Invalid float min for '$label'."; exit 1; fi
            if [[ -n $max ]] && ! is_float_literal "$max"; then log_err "Register Error: Invalid float max for '$label'."; exit 1; fi
            if [[ -n $step ]]; then
                if ! is_float_literal "$step" || ! LC_ALL=C awk -v v="$step" 'BEGIN { exit !(v + 0 > 0) }'; then
                    log_err "Register Error: Invalid float step for '$label'."
                    exit 1
                fi
            fi
            if [[ -n $min && -n $max ]] && ! number_le "$min" "$max"; then
                log_err "Register Error: min > max for '$label'."
                exit 1
            fi
            ;;
        cycle)
            validate_cycle_options "$label" "$min"
            ;;
    esac
    if [[ $type == action && ! $key =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
        log_err "Register Error: Action key '$key' is not a safe function suffix."
        exit 1
    fi
}

validate_item_default() {
    local label=$1 type=$2 min=$3 max=$4 value=$5 option valid=0
    local -a default_options=()
    if [[ $value == *$'\n'* || $value == *$'\r'* ]]; then
        log_err "Register Error: Multiline default for '$label'."; exit 1
    fi
    case $type in
        int|float)
            if [[ $type == int ]]; then is_int_literal "$value" && valid=1
            else is_float_literal "$value" && valid=1; fi
            if (( valid )) && [[ -n $min ]] && ! number_le "$min" "$value"; then valid=0; fi
            if (( valid )) && [[ -n $max ]] && ! number_le "$value" "$max"; then valid=0; fi
            ;;
        bool)
            case ${value,,} in true|false|yes|no|on|off|1|0) valid=1 ;; esac
            ;;
        cycle)
            cycle_display_value "$value" "$min"; value=$REPLY
            IFS=',' read -r -a default_options <<< "$min"
            for option in "${default_options[@]}"; do
                trim_spaces "$option"
                if [[ $value == "$REPLY" ]]; then valid=1; break; fi
            done
            ;;
        *) valid=1 ;;
    esac
    if (( !valid )); then
        log_err "Register Error: Invalid or out-of-range default for '$label'."; exit 1
    fi
}

register() {
    local -i tab_idx=$1
    local label=$2 config=$3 default_val=${4:-}
    local key type block min max step
    IFS='|' read -r key type block min max step <<< "$config"

    if (( tab_idx < 0 || tab_idx >= TAB_COUNT )); then
        log_err "Register Error: Tab index out of range for '$label': $tab_idx"
        exit 1
    fi
    validate_item_config "$label" "$key" "$type" "$block" "$min" "$max" "$step"
    if (( $# >= 4 )) && [[ $type != menu && $type != action ]]; then
        validate_item_default "$label" "$type" "$min" "$max" "$default_val"
    fi

    if [[ -n ${ITEM_MAP["${tab_idx}::${label}"]+_} ]]; then
        log_err "Register Error: Duplicate label in tab $tab_idx: $label"
        exit 1
    fi
    if [[ $type == menu && ! $key =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
        log_err "Register Error: Menu ID '$key' contains invalid characters."
        exit 1
    fi

    ITEM_MAP["${tab_idx}::${label}"]=$config
    if (( $# >= 4 )) && [[ $type != menu && $type != action ]]; then
        DEFAULTS["${tab_idx}::${label}"]=$default_val
    fi

    local -n _reg_tab_ref="TAB_ITEMS_${tab_idx}"
    _reg_tab_ref+=("$label")

    if [[ $type == menu ]]; then
        if declare -p "SUBMENU_ITEMS_${key}" >/dev/null 2>&1; then
            log_err "Register Error: Duplicate menu ID: $key"; exit 1
        fi
        declare -ga "SUBMENU_ITEMS_${key}=()"
    fi
}

register_child() {
    local parent_id=$1 label=$2 config=$3 default_val=${4:-}
    local key type block min max step
    IFS='|' read -r key type block min max step <<< "$config"

    if [[ ! $parent_id =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
        log_err "Register Error: Menu ID '$parent_id' contains invalid characters."
        exit 1
    fi
    if ! declare -p "SUBMENU_ITEMS_${parent_id}" >/dev/null 2>&1; then
        log_err "Register Error: register_child called for unknown menu '$parent_id'."
        exit 1
    fi
    validate_item_config "$label" "$key" "$type" "$block" "$min" "$max" "$step"
    if (( $# >= 4 )) && [[ $type != action && $type != menu ]]; then
        validate_item_default "$label" "$type" "$min" "$max" "$default_val"
    fi
    if [[ $type == menu ]]; then
        log_err "Register Error: Nested menus are not supported for '$label'."
        exit 1
    fi
    if [[ -n ${ITEM_MAP["${parent_id}::${label}"]+_} ]]; then
        log_err "Register Error: Duplicate label in menu '$parent_id': $label"
        exit 1
    fi

    ITEM_MAP["${parent_id}::${label}"]=$config
    if (( $# >= 4 )) && [[ $type != action ]]; then
        DEFAULTS["${parent_id}::${label}"]=$default_val
    fi

    local -n _child_ref="SUBMENU_ITEMS_${parent_id}"
    _child_ref+=("$label")
}

# =============================================================================
# GENERIC CONFIG CACHE PARSER
# =============================================================================

populate_config_cache() {
    local target_path=${WRITE_TARGET:-}
    local current_scope="" k v line before after
    local -A parsed_cache=()
    CONFIG_SIGNATURE=""

    if [[ -z $target_path || ! -f $target_path || ! -r $target_path ]]; then
        set_status "Config is missing or unreadable."
        return 1
    fi

    before=$(file_signature "$target_path") || { set_status "Unable to inspect config."; return 1; }
    while IFS= read -r line || [[ -n $line ]]; do
        trim_spaces "$line"; line=$REPLY
        [[ -z $line || $line == \#* || $line == \;* ]] && continue

        if [[ $line =~ ^\[(.*)\]$ ]]; then
            current_scope="${BASH_REMATCH[1]}"
            trim_spaces "$current_scope"; current_scope=$REPLY
        elif [[ $line =~ ^([^=[:space:]]+)[[:space:]]*=[[:space:]]*(.*)$ ]]; then
            k="${BASH_REMATCH[1]}"
            v="${BASH_REMATCH[2]}"
            trim_spaces "$k"; k=$REPLY
            trim_spaces "$v"; v=$REPLY
            if [[ $v == \"*\" || $v == \'*\' ]]; then
                v="${v:1:-1}"
            fi
            parsed_cache["${k}|${current_scope}"]=$v
        elif [[ $line =~ ^([^=[:space:]]+)[[:space:]]+(.*)$ ]]; then
            k=${BASH_REMATCH[1]}; v=${BASH_REMATCH[2]}
            trim_spaces "$v"; v=$REPLY
            if [[ $v == \"*\" || $v == \'*\' ]]; then v="${v:1:-1}"; fi
            parsed_cache["${k}|${current_scope}"]=$v
        fi
    done < "$target_path" || { set_status "Unable to read config."; return 1; }
    if ! after=$(file_signature "$target_path") || [[ $before != "$after" ]]; then
        set_status "Config changed while being read; retry."
        return 1
    fi
    # Publish only a complete, stable read. Failed reloads retain every view.
    CONFIG_CACHE=()
    for k in "${!parsed_cache[@]}"; do CONFIG_CACHE[$k]=${parsed_cache[$k]}; done
    CONFIG_SIGNATURE=$after
    return 0
}

# =============================================================================
# GENERIC CONFIG MUTATOR
# =============================================================================

write_value_to_file() {
    local target_key=$1 new_val=$2 target_scope=${3:-} operation=${4:-set}
    local cache_key lock_fd="" before after encoded
    LAST_WRITE_CHANGED=0
    trim_spaces "$target_scope"; target_scope=$REPLY
    cache_key="${target_key}|${target_scope}"
    if [[ $target_scope == *[$'\n\r'\[\]\|]* || $target_key == [\#\;\[]* ]]; then
        set_status "Invalid scope or key."; return 1
    fi
    if [[ $operation != set && $operation != delete ]] ||
       [[ $target_key == *[[:space:]=\|]* || -z $target_key ||
          $new_val == *$'\n'* || $new_val == *$'\r'* ]]; then
        set_status "Invalid key, operation, or multiline value."
        return 1
    fi
    if [[ -z $WRITE_TARGET || -z $LOCK_TARGET ]]; then
        set_status "Config path is not initialized."; return 1
    fi
    if ! exec {lock_fd}>>"$LOCK_TARGET"; then
        set_status "Unable to open config lock."; return 1
    fi
    if ! flock -x -n "$lock_fd"; then
        release_lock_fd "$lock_fd"
        set_status "Config file is locked by another process."; return 1
    fi
    # Validate the cache under the lock. Reload only after an external change;
    # unchanged large files need one streaming AWK mutation, no Bash reparse.
    if ! before=$(file_signature "$WRITE_TARGET"); then
        release_lock_fd "$lock_fd"
        set_status "Config is missing or unreadable."; return 1
    fi
    if [[ $before != "$CONFIG_SIGNATURE" ]]; then
        if ! populate_config_cache; then release_lock_fd "$lock_fd"; return 1; fi
        before=$CONFIG_SIGNATURE
    fi
    # Optional compare-and-swap protects relative edits, including stale no-ops.
    # Arguments 5/6 are the expected presence (0/1) and raw cached value.
    if (( $# >= 5 )); then
        local actual_present=0
        [[ ${CONFIG_CACHE[$cache_key]+present} ]] && actual_present=1
        if [[ $actual_present != "$5" || ${CONFIG_CACHE[$cache_key]-} != "${6-}" ]]; then
            release_lock_fd "$lock_fd"
            set_status "Setting changed externally; refreshed. Retry the adjustment."
            return 1
        fi
    fi
    if { [[ $operation == delete && ! ${CONFIG_CACHE[$cache_key]+present} ]]; } ||
       { [[ $operation == set && ${CONFIG_CACHE[$cache_key]+present} &&
            ${CONFIG_CACHE[$cache_key]} == "$new_val" ]]; }; then
        release_lock_fd "$lock_fd"; return 0
    fi
    if [[ ! -w $WRITE_TARGET ]] || ! create_tmpfile_for_target "$WRITE_TARGET"; then
        release_lock_fd "$lock_fd"
        set_status "Atomic save unavailable; check file and directory permissions."; return 1
    fi
    encoded=$new_val
    # Preserve whitespace and literal outer quotes in the documented grammar.
    if [[ $new_val == [[:space:]]* || $new_val == *[[:space:]] ||
          $new_val == \"*\" || $new_val == \'*\' ]]; then encoded="\"${new_val}\""; fi
    # ENVIRON preserves literal backslashes, unlike awk -v string assignments.
    if ! DUSKY_SCOPE="$target_scope" DUSKY_KEY="$target_key" DUSKY_VALUE="$encoded" \
         DUSKY_OPERATION="$operation" awk '
        BEGIN {
            scope = ENVIRON["DUSKY_SCOPE"]; key = ENVIRON["DUSKY_KEY"]
            val = ENVIRON["DUSKY_VALUE"]; deleting = ENVIRON["DUSKY_OPERATION"] == "delete"
            in_scope = (scope == ""); seen_scope = in_scope; found = 0
        }
        {
            if (NR == 1) eol = ($0 ~ /\r$/ ? "\r" : "")
            line = $0; sub(/^[[:space:]]+/, "", line); sub(/[[:space:]]+$/, "", line)
        }
        line ~ /^\[.*\]$/ {
            if (in_scope && !found && !deleting) { print key "=" val eol; found = 1 }
            sec = substr(line, 2, length(line) - 2)
            sub(/^[[:space:]]+/, "", sec); sub(/[[:space:]]+$/, "", sec)
            in_scope = (sec == scope); if (in_scope) seen_scope = 1
            print; next
        }
        {
            if (in_scope && line !~ /^[#;]/) {
                k = line; sub(/[=[:space:]].*$/, "", k)
                if (k == key && line ~ /[=[:space:]]/) {
                    if (!found && !deleting) {
                        indent = $0; sub(/[^[:space:]].*$/, "", indent)
                        rest = substr($0, length(indent) + length(key) + 1)
                        sub(/\r$/, "", rest)
                        # Keep the existing assignment operator and its spacing.
                        if (match(rest, /^[[:space:]]*=[[:space:]]*/)) sep = substr(rest, 1, RLENGTH)
                        else if (match(rest, /^[[:space:]]+/)) sep = substr(rest, 1, RLENGTH)
                        else sep = "="
                        print indent key sep val eol; found = 1
                    }
                    next
                }
            }
            print
        }
        END {
            if (!found && !deleting) {
                if (!seen_scope) print eol "\n[" scope "]" eol
                print key "=" val eol
            }
        }
    ' "$WRITE_TARGET" > "$_TMPFILE"; then
        remove_temp "$_TMPFILE"; release_lock_fd "$lock_fd"
        set_status "Failed to modify configuration."; return 1
    fi
    # Catch non-cooperating writers during staging; flock coordinates this engine.
    if ! after=$(file_signature "$WRITE_TARGET") || [[ $before != "$after" ]]; then
        remove_temp "$_TMPFILE"; release_lock_fd "$lock_fd"
        populate_config_cache || :
        set_status "Config changed during save; retry the edit."; return 1
    fi
    if ! commit_tmpfile_to_target "$WRITE_TARGET"; then
        remove_temp "$_TMPFILE"; release_lock_fd "$lock_fd"
        set_status "Atomic save failed."; return 1
    fi
    if [[ $operation == delete ]]; then
        unset 'CONFIG_CACHE[$cache_key]'
    else
        CONFIG_CACHE[$cache_key]=$new_val
    fi
    CONFIG_SIGNATURE=$(file_signature "$WRITE_TARGET") || CONFIG_SIGNATURE=""
    release_lock_fd "$lock_fd"
    LAST_WRITE_CHANGED=1
    return 0
}

# =============================================================================
# VALUE ENGINE
# =============================================================================

cycle_display_value() {
    local value=$1 options=$2 opt opt_dec
    local -a raw_opts=() opts=()
    REPLY=$value
    IFS=',' read -r -a raw_opts <<< "$options"
    for opt in "${raw_opts[@]}"; do
        trim_spaces "$opt"
        opts+=("$REPLY")
    done
    REPLY=$value
    for opt in "${opts[@]}"; do
        if [[ $opt == "$value" ]]; then
            REPLY=$opt
            return 0
        fi
    done
    if [[ $value =~ ^[0-9]+$ ]]; then
        for opt in "${opts[@]}"; do
            if [[ $opt =~ ^0[xX]([0-9a-fA-F]+)$ ]]; then
                opt_dec=$(( 16#${BASH_REMATCH[1]} ))
                if [[ $value == "$opt_dec" ]]; then
                    REPLY=$opt
                    return 0
                fi
            fi
        done
    fi
    return 0
}

load_active_values() {
    local REPLY_REF REPLY_CTX item key type block min cache_key norm_key norm_scope value
    get_active_context
    local -n _lav_items_ref="$REPLY_REF"

    for item in "${_lav_items_ref[@]}"; do
        local dummy_max dummy_step
        IFS='|' read -r key type block min dummy_max dummy_step <<< "${ITEM_MAP["${REPLY_CTX}::${item}"]}"
        normalize_target "$key" "$block"
        norm_key=$TARGET_KEY
        norm_scope=$TARGET_SCOPE
        cache_key="${norm_key}|${norm_scope}"
        if [[ -n ${CONFIG_CACHE[$cache_key]+_} ]]; then
            value=${CONFIG_CACHE[$cache_key]}
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
    [[ ${CONFIG_CACHE[$cache_key]+present} ]] && expected_present=1
    expected_value=${CONFIG_CACHE[$cache_key]-}
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

    if write_value_to_file "$key" "$new_val" "$block" set "$expected_present" "$expected_value"; then
        load_active_values
        clear_status
        if (( LAST_WRITE_CHANGED )); then post_write_action; fi
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
        if write_value_to_file "$key" "$def_val" "$block"; then
            load_active_values
            set_status "Reset '$label' to default ($def_val)."
            if (( LAST_WRITE_CHANGED )); then post_write_action; fi
        else
            set_status "Failed to reset '$label'."
        fi
    else
        if write_value_to_file "$key" "" "$block" delete; then
            load_active_values
            set_status "Reset '$label' to default (UNSET)."
            if (( LAST_WRITE_CHANGED )); then post_write_action; fi
        else
            set_status "Failed to reset '$label'."
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
    if write_value_to_file "$key" "$new_val" "$block" "$operation"; then
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
            if (( LAST_WRITE_CHANGED )); then any_written=1; fi
        else
            any_failed=1
        fi
    done

    if (( !any_failed )); then clear_status; fi
    if (( any_written )); then post_write_action; fi
    if (( any_failed )); then set_status "Some defaults were not written.${STATUS_MESSAGE:+ $STATUS_MESSAGE}"; fi
    return 0
}

# =============================================================================
# LINE INPUT AND SUDO
# =============================================================================

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

# =============================================================================
# RENDERING
# =============================================================================

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
    render_footer buf " File: $WRITE_TARGET"
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

# =============================================================================
# NAVIGATION AND INPUT
# =============================================================================

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
    if [[ -n $cb && $(type -t "$cb") == function ]]; then "$cb" "$chosen"; fi
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
            if [[ $(type -t "action_${key}") == function ]]; then
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
                write_value_to_file "$key" "" "$block" delete
            else
                write_value_to_file "$key" "$user_input" "$block"
            fi
            
            load_active_values
            if (( LAST_WRITE_CHANGED )); then post_write_action; fi
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

# Return a selection-only event for left press/motion, and a click only when
# release matches a press with no intervening motion or view change. Basic
# click-only terminals with SGR still provide pairs; no mode ACK is needed.
# SGR preserves the released button: left release is 0m. The generic release
# code 3 belongs to legacy mouse encodings, not the SGR format parsed here.
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
        if populate_config_cache; then
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

# =============================================================================
# ENTRYPOINT
# =============================================================================

parse_args() {
    while (($#)); do
        case $1 in
            --config)
                shift
                if [[ $# -gt 0 ]]; then CONFIG_FILE=$1; else log_err "--config requires a path"; exit 2; fi
                ;;
            --config=*)
                CONFIG_FILE=${1#--config=}
                [[ -n $CONFIG_FILE ]] || { log_err "--config requires a path"; exit 2; }
                ;;
            --help|-h)
                printf 'Usage: %s [--config /path/to/settings.conf]\n' "${0##*/}"
                exit 0
                ;;
            *)
                log_err "Unknown argument: $1"
                exit 2
                ;;
        esac
        shift
    done
}

main() {
    parse_args "$@"

    if (( TAB_COUNT == 0 || MAX_DISPLAY_ROWS < 1 )); then
        log_err "Configure at least one tab and one display row."; exit 1
    fi
    if (( BASH_VERSINFO[0] < 5 || (BASH_VERSINFO[0] == 5 && BASH_VERSINFO[1] < 3) )); then log_err "Bash 5.3+ required"; exit 1; fi
    if [[ ! -t 0 || ! -t 1 ]]; then log_err "Interactive TTY stdin/stdout required"; exit 1; fi

    local dep
    for dep in realpath mktemp flock stat chmod chown mv rm stty awk mkdir sha256sum; do
        if ! command -v "$dep" >/dev/null 2>&1; then log_err "Missing dependency: $dep"; exit 1; fi
    done

    resolve_write_target || exit 1
    register_items
    populate_config_cache || { log_err "$STATUS_MESSAGE"; exit 1; }

    ORIGINAL_STTY=$(stty -g < /dev/tty 2>/dev/null) || ORIGINAL_STTY=""
    if [[ -z $ORIGINAL_STTY ]]; then log_err "Failed to read terminal settings. A controlling TTY is required."; exit 1; fi
    if ! stty -icanon -echo -ixon min 1 time 0 < /dev/tty 2>/dev/null; then log_err "Failed to configure terminal raw input."; exit 1; fi

    TUI_STARTED=1
    printf '%s%s%s%s%s' "$ALT_SCREEN_ON" "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"
    
    # Keep nounset and pipefail; expected read/write failures are handled explicitly.
    # UI callbacks report errors instead of relying on errexit (which is context-sensitive).
    set +e
    load_active_values
    trap 'RESIZE_PENDING=1' WINCH CONT
    trap suspend_ui TSTP

    local key read_status
    local -i redraw=1
    update_terminal_size
    while true; do
        if (( RESIZE_PENDING )); then
            RESIZE_PENDING=0
            MOUSE_CLICK_PENDING=0
            update_terminal_size
            redraw=1
        fi
        if (( redraw )); then draw_ui; redraw=0; fi
        if IFS= read -rsn1 -t "$READ_LOOP_TIMEOUT" key < /dev/tty; then
            # Refresh geometry before applying input after a resize.
            if (( RESIZE_PENDING )); then
                RESIZE_PENDING=0; MOUSE_CLICK_PENDING=0; update_terminal_size
            fi
            handle_input_router "$key"
            redraw=1
        else
            read_status=$?
            if (( read_status == 1 )); then exit 0; fi
        fi
    done
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
