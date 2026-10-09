#!/usr/bin/env bash
# Shared schema registration, value helpers, and engine result fields.
# Source through frontend/ui.sh. One application/engine per Bash process.
# shellcheck disable=SC2034 # Shared fields are consumed by the frontend/engine.

declare -gA ENGINE_STATE=()
declare -g ENGINE_TARGET="" ENGINE_MESSAGE=""
declare -gi ENGINE_CHANGED=0

path_dirname() {
    local path=$1
    if [[ $path == */* ]]; then
        REPLY=${path%/*}
        [[ -n $REPLY ]] || REPLY=/
    else
        REPLY=.
    fi
}

trim_spaces() {
    local v=$1
    v=${v#"${v%%[![:space:]]*}"}
    v=${v%"${v##*[![:space:]]}"}
    REPLY=$v
}

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
