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
    printf "  \e[32m--disk\e[0m                     Show root disk usage\n"
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

# --- GUI EXECUTION / ROFI LAYOUT STYLING ---

# Width perfectly tuned to wrap the text and icons without dead space.
declare -a ROFI_CMD=(
    rofi -dmenu -i -no-custom -location 3
    -theme-str '
        window {
            width: 520px;
            x-offset: -20px;
            y-offset: 20px;
            padding: 24px;
            border-radius: 20px;
        }
        mainbox {
            spacing: 20px;
            children: [ inputbar, listview ];
        }
        inputbar {
            padding: 14px 20px;
            border-radius: 99px;
            spacing: 14px;
            children: [ prompt, entry ];
        }
        prompt {
            vertical-align: 0.5;
            font: "JetBrainsMono Nerd Font Bold 12";
        }
        entry {
            vertical-align: 0.5;
            placeholder: "Search Glance tools...";
            font: "JetBrainsMono Nerd Font 12";
        }
        listview {
            columns: 2;
            lines: 7;
            spacing: 12px;
            fixed-height: false;
            dynamic: true;
            scrollbar: false;
            flow: horizontal;
        }
        element {
            padding: 12px 20px;
            border-radius: 99px;
            cursor: pointer;
        }
        element-text {
            horizontal-align: 0.0;
            vertical-align: 0.5;
            cursor: inherit;
        }
    '
)

# Sub-menu styling optimized for single-column exact fits.
declare -a ROFI_SUB=(
    rofi -dmenu -i -no-custom -location 3
    -theme-str '
        window { 
            width: 380px; 
            x-offset: -20px;
            y-offset: 20px;
            padding: 20px; 
            border-radius: 20px; 
        }
        mainbox { 
            spacing: 16px; 
            children: [ inputbar, listview ]; 
        }
        inputbar { 
            padding: 12px 18px; 
            border-radius: 99px; 
            spacing: 12px;
            children: [ prompt, entry ]; 
        }
        prompt { 
            vertical-align: 0.5;
            font: "JetBrainsMono Nerd Font Bold 12"; 
        }
        entry { 
            vertical-align: 0.5;
        }
        listview { 
            lines: 5; 
            columns: 1; 
            spacing: 10px; 
            scrollbar: false; 
            fixed-height: false; 
            dynamic: true;
        }
        element { 
            padding: 12px 18px; 
            border-radius: 99px; 
        }
        element-text { 
            horizontal-align: 0.0; 
            vertical-align: 0.5;
        }
    '
)

# Prompt layout strictly for text-entry duration fields.
PROMPT_STYLE='window { width: 340px; x-offset: -20px; y-offset: 20px; padding: 20px; border-radius: 20px; } mainbox { children: [ inputbar ]; } inputbar { padding: 12px 18px; border-radius: 99px; spacing: 12px; children: [ prompt, entry ]; } prompt { vertical-align: 0.5; font: "JetBrainsMono Nerd Font Bold 12"; } entry { vertical-align: 0.5; placeholder: "Enter duration..."; } listview { lines: 0; }'

declare -a MENU_OPTIONS=(
    "󰜺  Stop / Clear"          "󰸉  Edit"
    "󱎫  Pomodoro"              "  CPU Usage"
    "󰔟  Timer"                 "󰘚  Memory (RAM)"
    "󱑎  Stopwatch"             "  CPU Temp"
    "󰥔  Live Clock"            "󰁹  Battery"
    "󰽽  Workspace"             "󰋊  Disk Usage"
    "󰈀  Network Speed"         "󰔚  System Uptime"
)

choice=$(printf '%s\n' "${MENU_OPTIONS[@]}" | "${ROFI_CMD[@]}" -p "Glance") || exit 0

case "$choice" in
    '󱎫  Pomodoro')
        last_pomo="1500:300"
        [[ -f "$POMO_STATE" ]] && last_pomo=$(<"$POMO_STATE")
        
        read -r lw_sec lb_sec <<< "$(parse_pomodoro "$last_pomo")"
        
        p_opts=(
            "󰐊  ($(fmt_t "$lw_sec") Work / $(fmt_t "$lb_sec") Break)"
            "󰒓  Set in Minutes"
            "󰒓  Set in Seconds"
        )
        pchoice=$(printf '%s\n' "${p_opts[@]}" | "${ROFI_SUB[@]}" -p "Pomodoro") || exit 0
        
        if [[ "$pchoice" == *"Start Last"* ]]; then
            "$DAEMON_SCRIPT" --pomodoro "$lw_sec" "$lb_sec" & disown
            
        elif [[ "$pchoice" == *"Minutes"* ]]; then
            w=$(rofi -dmenu -i -p "Work (Mins)" -location 3 -theme-str "$PROMPT_STYLE") || exit 0
            w=${w//[!0-9]/}; [[ -z "$w" ]] && exit 0
            
            b=$(rofi -dmenu -i -p "Break (Mins)" -location 3 -theme-str "$PROMPT_STYLE") || exit 0
            b=${b//[!0-9]/}; [[ -z "$b" ]] && b=0
            
            echo "$((w*60)):$((b*60))" > "$POMO_STATE"
            "$DAEMON_SCRIPT" --pomodoro "$((w*60))" "$((b*60))" & disown
            
        elif [[ "$pchoice" == *"Seconds"* ]]; then
            w=$(rofi -dmenu -i -p "Work (Secs)" -location 3 -theme-str "$PROMPT_STYLE") || exit 0
            w=${w//[!0-9]/}; [[ -z "$w" ]] && exit 0
            
            b=$(rofi -dmenu -i -p "Break (Secs)" -location 3 -theme-str "$PROMPT_STYLE") || exit 0
            b=${b//[!0-9]/}; [[ -z "$b" ]] && b=0
            
            echo "$w:$b" > "$POMO_STATE"
            "$DAEMON_SCRIPT" --pomodoro "$w" "$b" & disown
        fi
        ;;
        
    '󰔟  Timer')
        last_timer="15m"
        [[ -f "$TIMER_STATE" ]] && last_timer=$(<"$TIMER_STATE")
        
        lt_sec=$(parse_timer "$last_timer")
        
        t_opts=(
            "󰐊  Start Last ($(fmt_t "$lt_sec"))"
            "󰒓  Set in Minutes"
            "󰒓  Set in Seconds"
        )
        tchoice=$(printf '%s\n' "${t_opts[@]}" | "${ROFI_SUB[@]}" -p "Timer") || exit 0
        
        if [[ "$tchoice" == *"Start Last"* ]]; then
            "$DAEMON_SCRIPT" --timer "$lt_sec" & disown
            
        elif [[ "$tchoice" == *"Minutes"* ]]; then
            val=$(rofi -dmenu -i -p "Duration (Mins)" -location 3 -theme-str "$PROMPT_STYLE") || exit 0
            val=${val//[!0-9]/}; [[ -z "$val" ]] && exit 0
            
            echo "${val}m" > "$TIMER_STATE"
            "$DAEMON_SCRIPT" --timer "$((val*60))" & disown
            
        elif [[ "$tchoice" == *"Seconds"* ]]; then
            val=$(rofi -dmenu -i -p "Duration (Secs)" -location 3 -theme-str "$PROMPT_STYLE") || exit 0
            val=${val//[!0-9]/}; [[ -z "$val" ]] && exit 0
            
            echo "${val}s" > "$TIMER_STATE"
            "$DAEMON_SCRIPT" --timer "$val" & disown
        fi
        ;;
        
    '󰋊  Disk Usage')
        # Segmented Storage Categories
        st_opts=(
            "󰋊  Root Partition (/)"
            "󰆼  Solid State Drives (SSD)"
            "󰋊  Hard Disk Drives (HDD)"
        )
        stchoice=$(printf '%s\n' "${st_opts[@]}" | "${ROFI_SUB[@]}" -p "Storage Type") || exit 0

        if [[ "$stchoice" == *"Root Partition"* ]]; then
            "$DAEMON_SCRIPT" --disk & disown
            
        elif [[ "$stchoice" == *"Solid State Drives"* ]]; then
            declare -a ssd_opts=()
            
            # Robust AWK extraction guarantees reliable mapping regardless of spaces in model names
            while IFS=$'\t' read -r name model rota; do
                [[ "$name" =~ ^(loop|sr|ram|dm|fd) ]] && continue
                if [[ "$rota" == "0" ]]; then
                    ssd_opts+=("󰆼  $name (${model:-Unknown})")
                fi
            done < <(lsblk -d -n -o NAME,MODEL,ROTA | awk '{ r=$NF; n=$1; $1=""; $NF=""; sub(/^[ \t]+/, ""); sub(/[ \t]+$/, ""); print n "\t" $0 "\t" r }')

            [[ ${#ssd_opts[@]} -eq 0 ]] && ssd_opts=("󰜺  No SSDs found")
            
            dchoice=$(printf '%s\n' "${ssd_opts[@]}" | "${ROFI_SUB[@]}" -p "Select SSD") || exit 0
            [[ "$dchoice" == *"No SSDs"* ]] && exit 0
            
            dev_name=$(echo "$dchoice" | awk '{print $2}')
            rw_opts=("󰑍  Live Read" "󰏫  Live Write" "  Temperature")
            rwchoice=$(printf '%s\n' "${rw_opts[@]}" | "${ROFI_SUB[@]}" -p "/dev/$dev_name") || exit 0
            
            if [[ "$rwchoice" == *"Read"* ]]; then
                "$DAEMON_SCRIPT" --disk-read "$dev_name" & disown
            elif [[ "$rwchoice" == *"Write"* ]]; then
                "$DAEMON_SCRIPT" --disk-write "$dev_name" & disown
            elif [[ "$rwchoice" == *"Temperature"* ]]; then
                "$DAEMON_SCRIPT" --disk-temp "$dev_name" & disown
            fi

        elif [[ "$stchoice" == *"Hard Disk Drives"* ]]; then
            declare -a hdd_opts=()
            
            while IFS=$'\t' read -r name model rota; do
                [[ "$name" =~ ^(loop|sr|ram|dm|fd) ]] && continue
                if [[ "$rota" == "1" ]]; then
                    hdd_opts+=("󰋊  $name (${model:-Unknown})")
                fi
            done < <(lsblk -d -n -o NAME,MODEL,ROTA | awk '{ r=$NF; n=$1; $1=""; $NF=""; sub(/^[ \t]+/, ""); sub(/[ \t]+$/, ""); print n "\t" $0 "\t" r }')

            [[ ${#hdd_opts[@]} -eq 0 ]] && hdd_opts=("󰜺  No HDDs found")
            
            dchoice=$(printf '%s\n' "${hdd_opts[@]}" | "${ROFI_SUB[@]}" -p "Select HDD") || exit 0
            [[ "$dchoice" == *"No HDDs"* ]] && exit 0
            
            dev_name=$(echo "$dchoice" | awk '{print $2}')
            rw_opts=("󰑍  Live Read" "󰏫  Live Write" "  Temperature")
            rwchoice=$(printf '%s\n' "${rw_opts[@]}" | "${ROFI_SUB[@]}" -p "/dev/$dev_name") || exit 0
            
            if [[ "$rwchoice" == *"Read"* ]]; then
                "$DAEMON_SCRIPT" --disk-read "$dev_name" & disown
            elif [[ "$rwchoice" == *"Write"* ]]; then
                "$DAEMON_SCRIPT" --disk-write "$dev_name" & disown
            elif [[ "$rwchoice" == *"Temperature"* ]]; then
                "$DAEMON_SCRIPT" --disk-temp "$dev_name" & disown
            fi
        fi
        ;;

    '󱑎  Stopwatch')      "$DAEMON_SCRIPT" --stopwatch & disown ;;
    '󰥔  Live Clock')     "$DAEMON_SCRIPT" --clock & disown ;;
    '  CPU Usage')      "$DAEMON_SCRIPT" --cpu & disown ;;
    '󰘚  Memory (RAM)')
        m_opts=("󰘚  System RAM Usage" "󰘚  RAM Temperature" "󰘚  ZRAM Usage")
        mchoice=$(printf '%s\n' "${m_opts[@]}" | "${ROFI_SUB[@]}" -p "Memory") || exit 0

        if [[ "$mchoice" == *"System RAM"* ]]; then
            "$DAEMON_SCRIPT" --ram & disown
        elif [[ "$mchoice" == *"Temperature"* ]]; then
            "$DAEMON_SCRIPT" --ram-temp & disown
        elif [[ "$mchoice" == *"ZRAM"* ]]; then
            "$DAEMON_SCRIPT" --zram & disown
        fi
        ;;
    '  CPU Temp')       "$DAEMON_SCRIPT" --temp & disown ;;
    '󰁹  Battery / Power')"$DAEMON_SCRIPT" --battery & disown ;;
    '󰈀  Network Speed')  "$DAEMON_SCRIPT" --network & disown ;;
    '󰔚  System Uptime')  "$DAEMON_SCRIPT" --uptime & disown ;;
    '󰽽  Workspace')      "$DAEMON_SCRIPT" --workspace & disown ;;
    '󰸉  Edit')           foot --app-id=dusky_tui python ~/user_scripts/dusky_tui/python/main/main.py ~/user_scripts/mako_osd/dusky_glance/tui_glance_mako.py & disown ;;
    '󰜺  Stop / Clear')   "$DAEMON_SCRIPT" --stop & disown ;;
esac
