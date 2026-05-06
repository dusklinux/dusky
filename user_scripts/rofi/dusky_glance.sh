#!/usr/bin/env bash
# ==============================================================================
# DUSKY GLANCE - ROFI FRONTEND, SMART WRAPPER & STATE MANAGER
# ==============================================================================

set -euo pipefail

DAEMON_SCRIPT="$HOME/user_scripts/mako_osd/dusky_glance/dusky_glance_daemon.sh"

# --- CONFIGURATION STATE ---
SETTINGS_DIR="$HOME/.config/dusky/settings/dusky_glance"
mkdir -p "$SETTINGS_DIR"
TIMER_STATE="$SETTINGS_DIR/timer.state"
POMO_STATE="$SETTINGS_DIR/pomodoro.state"

# --- HELPER: TIME PARSERS ---
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
    
    # Defaults: 25 minutes (1500s) and 5 minutes (300s)
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

# --- CLI HELP MENU ---
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    printf "\e[1;34m::\e[0m \e[1mDusky Glance\e[0m - Smart HUD Wrapper\n\n"
    printf "\e[1mUSAGE:\e[0m\n  dusky_glance.sh [COMMAND]\n\n"
    printf "\e[1mCOMMANDS:\e[0m\n"
    printf "  \e[32m--pomodoro [work] [break]\e[0m  Start Pomodoro (e.g., 45 10)\n"
    printf "  \e[32m--timer [time]\e[0m             Start Timer (e.g., 90s, 15m)\n"
    printf "  \e[32m--stopwatch\e[0m                Start the stopwatch\n"
    printf "  \e[32m--clock\e[0m                    Show the live clock\n"
    printf "  \e[32m--cpu\e[0m                      Show live CPU usage\n"
    printf "  \e[32m--ram\e[0m                      Show live RAM usage\n"
    printf "  \e[32m--temp\e[0m                     Show CPU temperature\n"
    printf "  \e[32m--battery\e[0m                  Show battery status/power\n"
    printf "  \e[32m--network\e[0m                  Show live network speed\n"
    printf "  \e[32m--uptime\e[0m                   Show system uptime\n"
    printf "  \e[32m--workspace\e[0m                Show active Hyprland workspace\n"
    printf "  \e[31m--stop\e[0m                     Stop any running monitor\n"
    exit 0
fi

# --- HEADLESS PASSTHROUGH (KEYBINDINGS) ---
if (( $# > 0 )); then
    cmd="$1"
    case "$cmd" in
        --pomodoro)
            if [[ -n "${2:-}" ]]; then
                w_in="${2//[!0-9]/}"
                b_in="${3:-0}"
                b_in="${b_in//[!0-9]/}"
                [[ -z "$w_in" ]] && w_in=25
                [[ -z "$b_in" ]] && b_in=5
                
                # Input args typically denote minutes
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
declare -agr ROFI_CMD=(rofi -dmenu -i -no-custom -theme-str 'window {width: 20%;} listview {lines: 12;}')
declare -agr ROFI_SUB=(rofi -dmenu -i -no-custom -theme-str 'window {width: 35%;} listview {lines: 3;}')

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
            w=$(rofi -dmenu -i -p "Work Duration (Mins)" -theme-str 'window {width: 40%;} listview {lines: 0;} entry {placeholder: "";}') || exit 0
            w=${w//[!0-9]/}; [[ -z "$w" ]] && exit 0
            
            b=$(rofi -dmenu -i -p "Break Duration (Mins) [0 for none]" -theme-str 'window {width: 40%;} listview {lines: 0;} entry {placeholder: "";}') || exit 0
            b=${b//[!0-9]/}; [[ -z "$b" ]] && b=0
            
            echo "$((w*60)):$((b*60))" > "$POMO_STATE"
            "$DAEMON_SCRIPT" --pomodoro "$((w*60))" "$((b*60))" & disown
            
        elif [[ "$pchoice" == *"Seconds"* ]]; then
            w=$(rofi -dmenu -i -p "Work Duration (Secs)" -theme-str 'window {width: 40%;} listview {lines: 0;} entry {placeholder: "";}') || exit 0
            w=${w//[!0-9]/}; [[ -z "$w" ]] && exit 0
            
            b=$(rofi -dmenu -i -p "Break Duration (Secs) [0 for none]" -theme-str 'window {width: 40%;} listview {lines: 0;} entry {placeholder: "";}') || exit 0
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
        
    '⏱️  Stopwatch')      "$DAEMON_SCRIPT" --stopwatch & disown ;;
    '🕒  Live Clock')     "$DAEMON_SCRIPT" --clock & disown ;;
    '💻  CPU Usage')      "$DAEMON_SCRIPT" --cpu & disown ;;
    '🧠  Memory (RAM)')   "$DAEMON_SCRIPT" --ram & disown ;;
    '🌡️  CPU Temp')       "$DAEMON_SCRIPT" --temp & disown ;;
    '🔋  Battery / Power')"$DAEMON_SCRIPT" --battery & disown ;;
    '🌐  Network Speed')  "$DAEMON_SCRIPT" --network & disown ;;
    '🚀  System Uptime')  "$DAEMON_SCRIPT" --uptime & disown ;;
    '🖥️  Active Workspace')"$DAEMON_SCRIPT" --workspace & disown ;;
    '🛑  Stop / Clear')   "$DAEMON_SCRIPT" --stop & disown ;;
esac
