#!/usr/bin/env bash
# ==============================================================================
# DUSKY GLANCE - ROFI FRONTEND, SMART WRAPPER & STATE MANAGER
# ==============================================================================

set -Eeuo pipefail
shopt -s inherit_errexit

# --- CONFIGURATION STATE & PATHS ---
DAEMON_SCRIPT="$HOME/user_scripts/mako_osd/dusky_glance/dusky_glance_daemon.sh"
SETTINGS_DIR="$HOME/.config/dusky/settings/dusky_glance"
MAKO_CONF="${HOME}/.config/mako/config"

mkdir -p "$SETTINGS_DIR"
TIMER_STATE="$SETTINGS_DIR/timer.state"
POMO_STATE="$SETTINGS_DIR/pomodoro.state"

# --- EDITOR ARRAYS (READ-ONLY) ---
readonly -a GLANCE_MODULES=(
    "All Glance Modules"
    "dusky-glance-narrow"
    "dusky-glance-wide"
    "dusky-glance-timer"
)

readonly -a PROPERTIES=(
    "Text Color" "Background Color" "Border Color"
    "Border Size" "Border Radius"
    "Width" "Height" "Padding" "Margin" "Anchor"
)

readonly -a OPTS_COLORS=(
    "Unset (Matugen / Global Default)" "Transparent" "Black" "White"
    "Dark Grey" "Light Grey" "Red" "Green" "Blue"
    "Yellow" "Cyan" "Magenta" "Purple" "Orange"
)

readonly -a OPTS_BORDER_SIZE=( "Unset (Revert to Global)" "0" "1" "2" "3" "4" "5" )
readonly -a OPTS_BORDER_RADIUS=( "Unset (Revert to Global)" "0" "4" "8" "12" "16" "18" "20" "24" "30" )
readonly -a OPTS_WIDTH=( "Unset (Revert to Global)" "150" "174" "200" "210" "240" "280" "300" "350" "380" "400" )
readonly -a OPTS_HEIGHT=( "Unset (Revert to Global)" "30" "40" "48" "56" "64" "80" "100" "120" "150" )
readonly -a OPTS_PADDING=( "Unset (Revert to Global)" "0" "5" "10" "15" "20" "25" "30" )
readonly -a OPTS_MARGIN=( "Unset (Revert to Global)" "0" "10" "20" "0,20,20,0" "0,0,20,0" "0,0,50,0" "20,20,20,20" )
readonly -a OPTS_ANCHOR=( "Unset (Revert to Global)" "top-right" "top-center" "top-left" "bottom-right" "bottom-center" "bottom-left" "center" )

# --- SYSTEM & ROFI HELPERS ---
have_cmd() { command -v "$1" >/dev/null 2>&1; }

log_journal() {
    local priority=$1 message=$2
    have_cmd logger || return 0
    logger -p "user.${priority}" -t "dusky-glance-frontend" -- "$message" >/dev/null 2>&1 || return 0
}

notify_critical() {
    local title=$1 body=$2
    have_cmd notify-send || return 0
    notify-send -u critical -- "$title" "$body" >/dev/null 2>&1 || return 0
}

fatal() {
    local log_message=$1 notify_message=${2:-$1}
    log_journal err "$log_message"
    notify_critical "Glance Menu Error" "$notify_message"
    exit 1
}

array_contains() {
    local needle=$1
    local -n haystack=$2
    local item
    for item in "${haystack[@]}"; do
        [[ $item == "$needle" ]] && return 0
    done
    return 1
}

declare -agr ROFI_CMD=(rofi -dmenu -i -no-custom -theme-str 'window {width: 23%;} listview {lines: 13;}')
declare -agr ROFI_SUB=(rofi -dmenu -i -no-custom -theme-str 'window {width: 35%;} listview {lines: 3;}')
declare -agr ROFI_EDITOR_CMD=(rofi -dmenu -i -no-custom -theme-str 'window {width: 500px;}')

run_menu() {
    local prompt=$1 options_name=$2 output_name=$3
    local -n options_ref=$options_name
    local -n output_ref=$output_name
    local selected="" exit_code=0

    selected=$(
        printf '%s\n' "${options_ref[@]}" |
            "${ROFI_EDITOR_CMD[@]}" -p "$prompt"
    ) || exit_code=$?

    if [[ $exit_code -eq 0 ]]; then
        if [[ -z $selected ]]; then
            fatal "Empty selection for '$prompt'" "Invalid selection received."
        fi
        if ! array_contains "$selected" "$options_name"; then
            fatal "Invalid selection for '$prompt': $selected" "Invalid selection received."
        fi
        output_ref=$selected
        return 0
    fi

    [[ $exit_code -eq 1 || $exit_code -eq 130 || $exit_code -eq 143 ]] && return 1
    fatal "Rofi failed at '$prompt' with code $exit_code" "Rofi failed."
}

# --- TIME PARSERS ---
parse_timer() {
    local input="$1"
    local value="${input//[!0-9]/}"
    local unit="${input//[0-9]/}"
    
    [[ -z "$value" ]] && value=15
    [[ -z "$unit" ]] && unit="m"
    
    case "$unit" in
        s) echo "$value" ;;
        m) echo "$((value * 60))" ;;
        h) echo "$((value * 3600))" ;;
        *) echo "$((value * 60))" ;;
    esac
}

parse_pomodoro() {
    local input="$1"
    local work_s="${input%:*}"
    local break_s="${input#*:}"
    
    work_s="${work_s//[!0-9]/}"
    break_s="${break_s//[!0-9]/}"
    
    [[ -z "$work_s" ]] && work_s=1500
    [[ -z "$break_s" ]] && break_s=300
    
    echo "$work_s $break_s"
}

fmt_t() {
    local s="${1:-0}"
    local m=$((s / 60))
    local rm=$((s % 60))
    if (( m > 0 && rm > 0 )); then
        echo "${m}m ${rm}s"
    elif (( m > 0 )); then
        echo "${m}m"
    else
        echo "${s}s"
    fi
}

# --- CONFIG MODIFICATION ENGINE ---
apply_change() {
    local target_app="$1" config_key="$2" new_val="$3"
    local block_header="[app-name=${target_app}]"
    local tmp_file
    
    tmp_file=$(mktemp) || fatal "Failed to create temp file" "I/O Error."

    awk -v block="$block_header" -v key="$config_key" -v val="$new_val" '
    BEGIN { in_block = 0; key_found = 0 }
    {
        if ($0 == block) {
            in_block = 1; print $0; next
        }
        if (in_block && $0 ~ /^\[.*\]$/) {
            if (!key_found && val != "DELETE_KEY") { print key "=" val }
            in_block = 0; print $0; next
        }
        if (in_block && $0 ~ "^" key "=") {
            key_found = 1
            if (val != "DELETE_KEY") { print key "=" val }
            next
        }
        print $0
    }
    END {
        if (in_block && !key_found && val != "DELETE_KEY") { print key "=" val }
    }
    ' "$MAKO_CONF" > "$tmp_file"

    chmod --reference="$MAKO_CONF" "$tmp_file" 2>/dev/null || true
    mv -f "$tmp_file" "$MAKO_CONF" || fatal "Failed to write config" "I/O Error."
}

edit_osd_appearance() {
    [[ -f $MAKO_CONF && -w $MAKO_CONF ]] || fatal "Config not found or not writable: $MAKO_CONF"

    local sel_module sel_prop sel_val_label config_key actual_val

    run_menu "Target Module" GLANCE_MODULES sel_module || return 0
    run_menu "Property to Edit" PROPERTIES sel_prop || return 0

    case "$sel_prop" in
        "Text Color")       config_key="text-color";       run_menu "Text Color" OPTS_COLORS sel_val_label || return 0 ;;
        "Background Color") config_key="background-color"; run_menu "Background Color" OPTS_COLORS sel_val_label || return 0 ;;
        "Border Color")     config_key="border-color";     run_menu "Border Color" OPTS_COLORS sel_val_label || return 0 ;;
        "Border Size")      config_key="border-size";      run_menu "Border Size" OPTS_BORDER_SIZE sel_val_label || return 0 ;;
        "Border Radius")    config_key="border-radius";    run_menu "Border Radius" OPTS_BORDER_RADIUS sel_val_label || return 0 ;;
        "Width")            config_key="width";            run_menu "Width" OPTS_WIDTH sel_val_label || return 0 ;;
        "Height")           config_key="height";           run_menu "Height" OPTS_HEIGHT sel_val_label || return 0 ;;
        "Padding")          config_key="padding";          run_menu "Padding" OPTS_PADDING sel_val_label || return 0 ;;
        "Margin")           config_key="margin";           run_menu "Margin" OPTS_MARGIN sel_val_label || return 0 ;;
        "Anchor")           config_key="anchor";           run_menu "Anchor" OPTS_ANCHOR sel_val_label || return 0 ;;
    esac

    if [[ "$sel_val_label" == *"Unset"* ]]; then
        actual_val="DELETE_KEY"
    else
        if [[ "$sel_prop" == *"Color"* ]]; then
            case "$sel_val_label" in
                "Transparent") actual_val="#00000000" ;; "Black") actual_val="#000000" ;;
                "White")       actual_val="#FFFFFF" ;;   "Dark Grey") actual_val="#333333" ;;
                "Light Grey")  actual_val="#CCCCCC" ;;   "Red") actual_val="#FF3333" ;;
                "Green")       actual_val="#33FF33" ;;   "Blue") actual_val="#3333FF" ;;
                "Yellow")      actual_val="#FFFF33" ;;   "Cyan") actual_val="#33FFFF" ;;
                "Magenta")     actual_val="#FF33FF" ;;   "Purple") actual_val="#9933FF" ;;
                "Orange")      actual_val="#FF9933" ;;
            esac
        else
            actual_val="$sel_val_label"
        fi
    fi

    local -a targets=()
    if [[ "$sel_module" == "All Glance Modules" ]]; then
        for ((i=1; i<${#GLANCE_MODULES[@]}; i++)); do
            targets+=("${GLANCE_MODULES[$i]}")
        done
    else
        targets+=("$sel_module")
    fi

    for target in "${targets[@]}"; do
        log_journal info "Applying $config_key=$actual_val to $target"
        apply_change "$target" "$config_key" "$actual_val"
    done

    if have_cmd makoctl; then
        makoctl reload || log_journal err "makoctl reload failed"
    fi

    have_cmd notify-send && notify-send -t 3000 "Mako Glance Updated" "Applied '$sel_val_label' to $sel_prop."
}

# --- CLI HELP MENU ---
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    printf "\e[1;34m::\e[0m \e[1mDusky Glance\e[0m - Smart HUD Wrapper\n\n"
    printf "\e[1mUSAGE:\e[0m\n  dusky_glance.sh [COMMAND]\n\n"
    printf "\e[1mCOMMANDS:\e[0m\n"
    printf "  \e[32m--pomodoro [w] [b]\e[0m Start Pomodoro\n"
    printf "  \e[32m--timer [time]\e[0m     Start Timer\n"
    printf "  \e[32m--stopwatch\e[0m        Start Stopwatch\n"
    printf "  \e[32m--clock\e[0m            Live Clock\n"
    printf "  \e[32m--cpu\e[0m              CPU usage\n"
    printf "  \e[32m--ram\e[0m              RAM usage\n"
    printf "  \e[32m--temp\e[0m             CPU temp\n"
    printf "  \e[32m--battery\e[0m          Battery status\n"
    printf "  \e[32m--network\e[0m          Network speed\n"
    printf "  \e[32m--uptime\e[0m           System uptime\n"
    printf "  \e[32m--workspace\e[0m        Active Workspace\n"
    printf "  \e[31m--stop\e[0m             Stop active monitor\n"
    exit 0
fi

# --- HEADLESS PASSTHROUGH (KEYBINDINGS) ---
if (( $# > 0 )); then
    cmd="$1"
    case "$cmd" in
        --pomodoro)
            if [[ -n "${2:-}" ]]; then
                w_in="${2//[!0-9]/}"; b_in="${3:-0}"; b_in="${b_in//[!0-9]/}"
                [[ -z "$w_in" ]] && w_in=25; [[ -z "$b_in" ]] && b_in=5
                echo "$((w_in * 60)):$((b_in * 60))" > "$POMO_STATE"
                "$DAEMON_SCRIPT" --pomodoro "$((w_in * 60))" "$((b_in * 60))" & disown
            else
                last_pomo="1500:300"
                [[ -f "$POMO_STATE" ]] && last_pomo=$(<"$POMO_STATE")
                read -r work_s break_s <<< "$(parse_pomodoro "$last_pomo")"
                "$DAEMON_SCRIPT" --pomodoro "$work_s" "$break_s" & disown
            fi
            ;;
        --timer)
            if [[ -n "${2:-}" ]]; then
                echo "$2" > "$TIMER_STATE"
                secs=$(parse_timer "$2")
                "$DAEMON_SCRIPT" --timer "$secs" & disown
            else
                last_timer="15m"
                [[ -f "$TIMER_STATE" ]] && last_timer=$(<"$TIMER_STATE")
                secs=$(parse_timer "$last_timer")
                "$DAEMON_SCRIPT" --timer "$secs" & disown
            fi
            ;;
        *)
            "$DAEMON_SCRIPT" "$@" & disown
            ;;
    esac
    exit 0
fi

# --- GUI EXECUTION ---
declare -agr MENU_OPTIONS=(
    '🍅  Pomodoro'
    '⏳  Timer'
    '⏱️  Stopwatch'
    '🕒  Live Clock'
    '💻  CPU Usage'
    '🧠  Memory (RAM)'
    '🌡️  CPU Temp'
    '🔋  Battery / Power'
    '🌐  Network Speed'
    '🚀  System Uptime'
    '🖥️  Active Workspace'
    '🎨  Customize Appearance'
    '🛑  Stop / Clear'
)

choice=$(printf '%s\n' "${MENU_OPTIONS[@]}" | "${ROFI_CMD[@]}" -p "Glance") || exit 0

case "$choice" in
    '🍅  Pomodoro')
        last_pomo="1500:300"
        [[ -f "$POMO_STATE" ]] && last_pomo=$(<"$POMO_STATE")
        read -r lw_sec lb_sec <<< "$(parse_pomodoro "$last_pomo")"
        
        p_opts=(
            "▶️  Start Last ($(fmt_t "$lw_sec") Work / $(fmt_t "$lb_sec") Break)"
            "⚙️  Set in Minutes"
            "⚙️  Set in Seconds"
        )
        pchoice=$(printf '%s\n' "${p_opts[@]}" | "${ROFI_SUB[@]}" -p "Pomodoro") || exit 0
        
        if [[ "$pchoice" == *"Start Last"* ]]; then
            "$DAEMON_SCRIPT" --pomodoro "$lw_sec" "$lb_sec" & disown
        elif [[ "$pchoice" == *"Minutes"* ]]; then
            w=$(rofi -dmenu -i -p "Work (Mins)" -theme-str 'window {width: 40%;} listview {lines: 0;} entry {placeholder: "";}') || exit 0
            w=${w//[!0-9]/}; [[ -z "$w" ]] && exit 0
            b=$(rofi -dmenu -i -p "Break (Mins) [0 for none]" -theme-str 'window {width: 40%;} listview {lines: 0;} entry {placeholder: "";}') || exit 0
            b=${b//[!0-9]/}; [[ -z "$b" ]] && b=0
            echo "$((w*60)):$((b*60))" > "$POMO_STATE"
            "$DAEMON_SCRIPT" --pomodoro "$((w*60))" "$((b*60))" & disown
        elif [[ "$pchoice" == *"Seconds"* ]]; then
            w=$(rofi -dmenu -i -p "Work (Secs)" -theme-str 'window {width: 40%;} listview {lines: 0;} entry {placeholder: "";}') || exit 0
            w=${w//[!0-9]/}; [[ -z "$w" ]] && exit 0
            b=$(rofi -dmenu -i -p "Break (Secs) [0 for none]" -theme-str 'window {width: 40%;} listview {lines: 0;} entry {placeholder: "";}') || exit 0
            b=${b//[!0-9]/}; [[ -z "$b" ]] && b=0
            echo "$w:$b" > "$POMO_STATE"
            "$DAEMON_SCRIPT" --pomodoro "$w" "$b" & disown
        fi
        ;;
        
    '⏳  Timer')
        last_timer="15m"
        [[ -f "$TIMER_STATE" ]] && last_timer=$(<"$TIMER_STATE")
        lt_sec=$(parse_timer "$last_timer")
        
        t_opts=(
            "▶️  Start Last ($(fmt_t "$lt_sec"))"
            "⚙️  Set in Minutes"
            "⚙️  Set in Seconds"
        )
        tchoice=$(printf '%s\n' "${t_opts[@]}" | "${ROFI_SUB[@]}" -p "Timer") || exit 0
        
        if [[ "$tchoice" == *"Start Last"* ]]; then
            "$DAEMON_SCRIPT" --timer "$lt_sec" & disown
        elif [[ "$tchoice" == *"Minutes"* ]]; then
            val=$(rofi -dmenu -i -p "Duration (Mins)" -theme-str 'window {width: 40%;} listview {lines: 0;} entry {placeholder: "";}') || exit 0
            val=${val//[!0-9]/}; [[ -z "$val" ]] && exit 0
            echo "${val}m" > "$TIMER_STATE"
            "$DAEMON_SCRIPT" --timer "$((val*60))" & disown
        elif [[ "$tchoice" == *"Seconds"* ]]; then
            val=$(rofi -dmenu -i -p "Duration (Secs)" -theme-str 'window {width: 40%;} listview {lines: 0;} entry {placeholder: "";}') || exit 0
            val=${val//[!0-9]/}; [[ -z "$val" ]] && exit 0
            echo "${val}s" > "$TIMER_STATE"
            "$DAEMON_SCRIPT" --timer "$val" & disown
        fi
        ;;
        
    '⏱️  Stopwatch')          "$DAEMON_SCRIPT" --stopwatch & disown ;;
    '🕒  Live Clock')         "$DAEMON_SCRIPT" --clock & disown ;;
    '💻  CPU Usage')          "$DAEMON_SCRIPT" --cpu & disown ;;
    '🧠  Memory (RAM)')       "$DAEMON_SCRIPT" --ram & disown ;;
    '🌡️  CPU Temp')           "$DAEMON_SCRIPT" --temp & disown ;;
    '🔋  Battery / Power')    "$DAEMON_SCRIPT" --battery & disown ;;
    '🌐  Network Speed')      "$DAEMON_SCRIPT" --network & disown ;;
    '🚀  System Uptime')      "$DAEMON_SCRIPT" --uptime & disown ;;
    '🖥️  Active Workspace')    "$DAEMON_SCRIPT" --workspace & disown ;;
    '🎨  Customize Appearance') edit_osd_appearance ;;
    '🛑  Stop / Clear')       "$DAEMON_SCRIPT" --stop & disown ;;
esac
