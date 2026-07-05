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
RECENTS_STATE="$SETTINGS_DIR/recents"

# --- HELPER: SAVE RECENT ---
save_recent() {
    local label="$1"
    local cmd_args="$2"
    
    local temp_file
    temp_file=$(mktemp)
    if [[ -f "$RECENTS_STATE" ]]; then
        grep -v -F -e "$label|" -e "|$cmd_args" "$RECENTS_STATE" > "$temp_file" || true
    fi
    (echo "$label|$cmd_args"; cat "$temp_file" 2>/dev/null || true) | head -n 5 > "$RECENTS_STATE"
    rm -f "$temp_file"
}

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
    printf "  \e[32m--clock-short\e[0m              Show the live clock (no seconds)\n"
    printf "  \e[32m--world-clock [tz] [lbl]\e[0m   Show live world clock\n"
    printf "  \e[32m--cpu-power\e[0m                Show live CPU Package Power (Watts)\n"
    printf "  \e[32m--cpu\e[0m                      Show live CPU usage\n"
    printf "  \e[32m--ram\e[0m                      Show live RAM usage\n"
    printf "  \e[32m--temp\e[0m                     Show CPU temperature\n"
    printf "  \e[32m--battery\e[0m                  Show battery status/power\n"
    printf "  \e[32m--disk\e[0m                     Show root disk usage\n"
    printf "  \e[32m--network\e[0m                  Show live network speed\n"
    printf "  \e[32m--uptime\e[0m                   Show system uptime\n"
    printf "  \e[32m--workspace\e[0m                Show active Hyprland workspace\n"
    printf "  \e[32m--hud [card] [vendor]\e[0m      Show live Gaming HUD\n"
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
            lines: 6;
            spacing: 12px;
            fixed-height: false;
            dynamic: true;
            scrollbar: false;
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
            lines: 6; 
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

# Flow is vertical by default (so 0..5 in Col 1, and 6..11 in Col 2)
# This maps Row 1 Col 1 to Index 0, and Row 1 Col 2 to Index 6.
declare -a MENU_OPTIONS=(
    "󰜺  Stop / Clear"      # Index 0 (Col 1 Row 1)
    "󰸉  Edit"              # Index 1 (Col 1 Row 2)
    "  CPU"               # Index 2 (Col 1 Row 3)
    "󰢮  GPU"               # Index 3 (Col 1 Row 4)
    "󰋊  Disk Usage"        # Index 4 (Col 1 Row 5)
    "󰽽  Workspace"         # Index 5 (Col 1 Row 6)
    "󰕳  Recents"           # Index 6 (Col 2 Row 1)
    "󰔟  Time & Focus"      # Index 7 (Col 2 Row 2)
    "󰘚  Memory (RAM)"      # Index 8 (Col 2 Row 3)
    "󰁹  Battery"           # Index 9 (Col 2 Row 4)
    "󰈀  Network Speed"     # Index 10 (Col 2 Row 5)
    "  Gaming HUD"        # Index 11 (Col 2 Row 6)
)

while true; do
    choice=$(printf '%s\n' "${MENU_OPTIONS[@]}" | "${ROFI_CMD[@]}" -p "Glance") || exit 0

    case "$choice" in
        '󰕳  Recents')
            while true; do
                if [[ ! -f "$RECENTS_STATE" ]] || [[ ! -s "$RECENTS_STATE" ]]; then
                    rofi -e "No recently used items."
                    break
                fi
                
                recent_lines=()
                while IFS= read -r line; do
                    [[ -n "$line" ]] && recent_lines+=("$line")
                done < "$RECENTS_STATE"
                
                declare -a recent_opts=("  Back")
                for entry in "${recent_lines[@]}"; do
                    IFS='|' read -r r_label r_cmd <<< "$entry"
                    recent_opts+=("$r_label")
                done
                
                rchoice=$(printf '%s\n' "${recent_opts[@]}" | "${ROFI_SUB[@]}" -p "Recents") || break
                [[ "$rchoice" == "  Back" ]] && break
                
                for entry in "${recent_lines[@]}"; do
                    IFS='|' read -r r_label r_cmd <<< "$entry"
                    if [[ "$rchoice" == "$r_label" ]]; then
                        save_recent "$r_label" "$r_cmd"
                        "$DAEMON_SCRIPT" $r_cmd & disown
                        exit 0
                    fi
                done
            done
            ;;

        '  Gaming HUD')
            # Dynamic GPU Scan for HUD
            gpu_list=()
            for card in /sys/class/drm/card[0-9]; do
                [[ -r "$card/device/vendor" ]] || continue
                vendor=$(cat "$card/device/vendor")
                vendor_lbl=""
                case "${vendor,,}" in
                    0x8086) vendor_lbl="Intel" ;;
                    0x1002) vendor_lbl="AMD" ;;
                    0x10de) vendor_lbl="NVIDIA" ;;
                    *)      vendor_lbl="GPU" ;;
                esac
                
                power_state=""
                [[ -r "$card/device/power_state" ]] && power_state=$(cat "$card/device/power_state" 2>/dev/null)
                
                card_name=""
                if [[ "$power_state" != D3* ]]; then
                    sys_device_path=$(readlink -f "$card/device" 2>/dev/null || true)
                    pci_address="${sys_device_path##*/}"
                    if [[ -n "$pci_address" ]] && command -v lspci >/dev/null 2>&1; then
                        card_name=$(lspci -s "$pci_address" 2>/dev/null | sed -E 's/^[0-9a-fA-F:.]+ [^:]+: //' || true)
                    fi
                fi
                [[ -z "$card_name" ]] && card_name="${vendor_lbl} GPU"
                
                boot_vga=0
                [[ -r "$card/device/boot_vga" ]] && boot_vga=$(cat "$card/device/boot_vga" 2>/dev/null)
                
                if [[ "$boot_vga" == "1" ]]; then
                    gpu_list=("${card##*/}|$card_name|$vendor_lbl|$power_state" "${gpu_list[@]:-}")
                else
                    gpu_list+=("${card##*/}|$card_name|$vendor_lbl|$power_state")
                fi
            done
            
            selected_card=""
            selected_vendor=""
            
            if [[ ${#gpu_list[@]} -eq 0 ]]; then
                rofi -e "No GPUs detected."
                exit 1
            elif [[ ${#gpu_list[@]} -eq 1 ]]; then
                IFS='|' read -r selected_card selected_name selected_vendor selected_pstate <<< "${gpu_list[0]}"
            else
                while true; do
                    declare -a card_opts=("  Back")
                    for entry in "${gpu_list[@]}"; do
                        IFS='|' read -r c_node c_name c_vend c_pstate <<< "$entry"
                        if [[ "$c_pstate" == D3* ]]; then
                            card_opts+=("󰤄  $c_vend ($c_pstate)")
                        else
                            card_opts+=("󰢮  $c_vend (Active)")
                        fi
                    done
                    
                    cardchoice=$(printf '%s\n' "${card_opts[@]}" | "${ROFI_SUB[@]}" -p "Select HUD GPU") || break
                    [[ "$cardchoice" == "  Back" ]] && break
                    
                    for entry in "${gpu_list[@]}"; do
                        IFS='|' read -r c_node c_name c_vend c_pstate <<< "$entry"
                        if [[ "$cardchoice" == *"$c_vend"* ]]; then
                            selected_card="$c_node"
                            selected_vendor="$c_vend"
                            break
                        fi
                    done
                    [[ -n "$selected_card" ]] && break
                done
            fi
            
            [[ -z "$selected_card" ]] && continue
            
            save_recent "Gaming HUD ($selected_vendor)" "--hud $selected_card $selected_vendor"
            "$DAEMON_SCRIPT" --hud "$selected_card" "$selected_vendor" & disown
            exit 0
            ;;

        '󰢮  GPU')
            # Dynamic GPU Scan
            gpu_list=()
            for card in /sys/class/drm/card[0-9]; do
                [[ -r "$card/device/vendor" ]] || continue
                vendor=$(cat "$card/device/vendor")
                vendor_lbl=""
                case "${vendor,,}" in
                    0x8086) vendor_lbl="Intel" ;;
                    0x1002) vendor_lbl="AMD" ;;
                    0x10de) vendor_lbl="NVIDIA" ;;
                    *)      vendor_lbl="GPU" ;;
                esac
                
                power_state=""
                [[ -r "$card/device/power_state" ]] && power_state=$(cat "$card/device/power_state" 2>/dev/null)
                
                card_name=""
                if [[ "$power_state" != D3* ]]; then
                    sys_device_path=$(readlink -f "$card/device" 2>/dev/null || true)
                    pci_address="${sys_device_path##*/}"
                    if [[ -n "$pci_address" ]] && command -v lspci >/dev/null 2>&1; then
                        card_name=$(lspci -s "$pci_address" 2>/dev/null | sed -E 's/^[0-9a-fA-F:.]+ [^:]+: //' || true)
                    fi
                fi
                [[ -z "$card_name" ]] && card_name="${vendor_lbl} GPU"
                
                boot_vga=0
                [[ -r "$card/device/boot_vga" ]] && boot_vga=$(cat "$card/device/boot_vga" 2>/dev/null)
                
                if [[ "$boot_vga" == "1" ]]; then
                    gpu_list=("${card##*/}|$card_name|$vendor_lbl|$power_state" "${gpu_list[@]:-}")
                else
                    gpu_list+=("${card##*/}|$card_name|$vendor_lbl|$power_state")
                fi
            done
            
            if [[ ${#gpu_list[@]} -eq 0 ]]; then
                rofi -e "No GPUs detected."
                exit 1
            fi
            
            while true; do
                selected_card=""
                selected_vendor=""
                selected_name=""
                selected_pstate=""
                
                if [[ ${#gpu_list[@]} -eq 1 ]]; then
                    IFS='|' read -r selected_card selected_name selected_vendor selected_pstate <<< "${gpu_list[0]}"
                else
                    declare -a card_opts=("  Back")
                    for entry in "${gpu_list[@]}"; do
                        IFS='|' read -r c_node c_name c_vend c_pstate <<< "$entry"
                        if [[ "$c_pstate" == D3* ]]; then
                            card_opts+=("󰤄  $c_vend ($c_pstate)")
                        else
                            card_opts+=("󰢮  $c_vend (Active)")
                        fi
                    done
                    
                    cardchoice=$(printf '%s\n' "${card_opts[@]}" | "${ROFI_SUB[@]}" -p "GPU") || break
                    [[ "$cardchoice" == "  Back" ]] && break
                    
                    for entry in "${gpu_list[@]}"; do
                        IFS='|' read -r c_node c_name c_vend c_pstate <<< "$entry"
                        if [[ "$cardchoice" == *"$c_vend"* ]]; then
                            selected_card="$c_node"
                            selected_name="$c_name"
                            selected_vendor="$c_vend"
                            selected_pstate="$c_pstate"
                            break
                        fi
                    done
                fi
                
                [[ -z "$selected_card" ]] && break
                
                while true; do
                    gpu_opts=(
                        "  Back"
                        "󱐋  GPU Power (Watts)"
                        "󰢮  GPU Usage"
                        "󰘚  GPU Memory"
                    )
                    gpuchoice=$(printf '%s\n' "${gpu_opts[@]}" | "${ROFI_SUB[@]}" -p "$selected_vendor") || break
                    if [[ "$gpuchoice" == "  Back" ]]; then
                        if [[ ${#gpu_list[@]} -eq 1 ]]; then
                            break 2
                        else
                            break
                        fi
                    fi
                    
                    if [[ "$gpuchoice" == *"GPU Power"* ]]; then
                        save_recent "GPU Power ($selected_vendor)" "--gpu-power $selected_card $selected_vendor"
                        "$DAEMON_SCRIPT" --gpu-power "$selected_card" "$selected_vendor" & disown
                        exit 0
                    elif [[ "$gpuchoice" == *"GPU Usage"* ]]; then
                        save_recent "GPU Usage ($selected_vendor)" "--gpu-usage $selected_card $selected_vendor"
                        "$DAEMON_SCRIPT" --gpu-usage "$selected_card" "$selected_vendor" & disown
                        exit 0
                    elif [[ "$gpuchoice" == *"GPU Memory"* ]]; then
                        save_recent "GPU Memory ($selected_vendor)" "--gpu-mem $selected_card $selected_vendor"
                        "$DAEMON_SCRIPT" --gpu-mem "$selected_card" "$selected_vendor" & disown
                        exit 0
                    fi
                done
                
                [[ ${#gpu_list[@]} -eq 1 ]] && break
            done
            continue
            ;;

        '󰔟  Time & Focus')
            while true; do
                tf_opts=(
                    "  Back"
                    "󰥔  Clock (no seconds)"
                    "󰥔  Clock (with seconds)"
                    "󰥔  World Clock"
                    "󰔟  Timer"
                    "󰔚  System Uptime"
                    "󱑎  Stopwatch"
                    "󱎫  Pomodoro"
                )
                tfchoice=$(printf '%s\n' "${tf_opts[@]}" | "${ROFI_SUB[@]}" -p "Time & Focus") || break
                [[ "$tfchoice" == "  Back" ]] && break
                
                case "$tfchoice" in
                    *"Clock (no seconds)"*)
                        save_recent "Clock (no seconds)" "--clock-short"
                        "$DAEMON_SCRIPT" --clock-short & disown
                        exit 0
                        ;;
                    *"Clock (with seconds)"*)
                        save_recent "Clock (with seconds)" "--clock"
                        "$DAEMON_SCRIPT" --clock & disown
                        exit 0
                        ;;
                    *"World Clock"*)
                        while true; do
                            wc_opts=(
                                "  Back"
                                "🇯🇵  Japan (Tokyo)"
                                "🇺🇸  New York (East)"
                                "🇺🇸  Chicago (Central)"
                                "🇺🇸  Denver (Mountain)"
                                "🇺🇸  California (West)"
                                "🇬🇧  London"
                                "🇨🇳  Beijing"
                                "🇦🇺  Australia (Sydney)"
                                "🇦🇪  Dubai"
                                "🇷🇺  Moscow"
                                "🇸🇬  Singapore"
                            )
                            wcchoice=$(printf '%s\n' "${wc_opts[@]}" | "${ROFI_SUB[@]}" -p "World Clock") || break
                            [[ "$wcchoice" == "  Back" ]] && break
                            
                            case "$wcchoice" in
                                *"New York"*)
                                    save_recent "World Clock (New York)" "--world-clock America/New_York NY"
                                    "$DAEMON_SCRIPT" --world-clock "America/New_York" "NY" & disown
                                    exit 0
                                    ;;
                                *"Chicago"*)
                                    save_recent "World Clock (Chicago)" "--world-clock America/Chicago Chicago"
                                    "$DAEMON_SCRIPT" --world-clock "America/Chicago" "Chicago" & disown
                                    exit 0
                                    ;;
                                *"Denver"*)
                                    save_recent "World Clock (Denver)" "--world-clock America/Denver Denver"
                                    "$DAEMON_SCRIPT" --world-clock "America/Denver" "Denver" & disown
                                    exit 0
                                    ;;
                                *"California"*)
                                    save_recent "World Clock (California)" "--world-clock America/Los_Angeles California"
                                    "$DAEMON_SCRIPT" --world-clock "America/Los_Angeles" "California" & disown
                                    exit 0
                                    ;;
                                *"London"*)
                                    save_recent "World Clock (London)" "--world-clock Europe/London London"
                                    "$DAEMON_SCRIPT" --world-clock "Europe/London" "London" & disown
                                    exit 0
                                    ;;
                                *"Beijing"*)
                                    save_recent "World Clock (Beijing)" "--world-clock Asia/Shanghai Beijing"
                                    "$DAEMON_SCRIPT" --world-clock "Asia/Shanghai" "Beijing" & disown
                                    exit 0
                                    ;;
                                *"Australia"*)
                                    save_recent "World Clock (Sydney)" "--world-clock Australia/Sydney Sydney"
                                    "$DAEMON_SCRIPT" --world-clock "Australia/Sydney" "Sydney" & disown
                                    exit 0
                                    ;;
                                *"Dubai"*)
                                    save_recent "World Clock (Dubai)" "--world-clock Asia/Dubai Dubai"
                                    "$DAEMON_SCRIPT" --world-clock "Asia/Dubai" "Dubai" & disown
                                    exit 0
                                    ;;
                                *"Moscow"*)
                                    save_recent "World Clock (Moscow)" "--world-clock Europe/Moscow Moscow"
                                    "$DAEMON_SCRIPT" --world-clock "Europe/Moscow" "Moscow" & disown
                                    exit 0
                                    ;;
                                *"Japan"*)
                                    save_recent "World Clock (Tokyo)" "--world-clock Asia/Tokyo Tokyo"
                                    "$DAEMON_SCRIPT" --world-clock "Asia/Tokyo" "Tokyo" & disown
                                    exit 0
                                    ;;
                                *"Singapore"*)
                                    save_recent "World Clock (Singapore)" "--world-clock Asia/Singapore Singapore"
                                    "$DAEMON_SCRIPT" --world-clock "Asia/Singapore" "Singapore" & disown
                                    exit 0
                                    ;;
                            esac
                        done
                        ;;
                    *"Timer"*)
                        while true; do
                            last_timer="15m"
                            [[ -f "$TIMER_STATE" ]] && last_timer=$(<"$TIMER_STATE")
                            lt_sec=$(parse_timer "$last_timer")
                            t_opts=(
                                "  Back"
                                "󰐊  Start Last ($(fmt_t "$lt_sec"))"
                                "󰒓  Set in Minutes"
                                "󰒓  Set in Seconds"
                            )
                            tchoice=$(printf '%s\n' "${t_opts[@]}" | "${ROFI_SUB[@]}" -p "Timer") || break
                            [[ "$tchoice" == "  Back" ]] && break
                            
                            if [[ "$tchoice" == *"Start Last"* ]]; then
                                save_recent "Timer ($(fmt_t "$lt_sec"))" "--timer $lt_sec"
                                "$DAEMON_SCRIPT" --timer "$lt_sec" & disown
                                exit 0
                            elif [[ "$tchoice" == *"Minutes"* ]]; then
                                val=$(rofi -dmenu -i -p "Duration (Mins)" -location 3 -theme-str "$PROMPT_STYLE") || continue
                                val=${val//[!0-9]/}; [[ -z "$val" ]] && continue
                                echo "${val}m" > "$TIMER_STATE"
                                save_recent "Timer (${val}m)" "--timer $((val*60))"
                                "$DAEMON_SCRIPT" --timer "$((val*60))" & disown
                                exit 0
                            elif [[ "$tchoice" == *"Seconds"* ]]; then
                                val=$(rofi -dmenu -i -p "Duration (Secs)" -location 3 -theme-str "$PROMPT_STYLE") || continue
                                val=${val//[!0-9]/}; [[ -z "$val" ]] && continue
                                echo "${val}s" > "$TIMER_STATE"
                                save_recent "Timer (${val}s)" "--timer $val"
                                "$DAEMON_SCRIPT" --timer "$val" & disown
                                exit 0
                            fi
                        done
                        ;;
                    *"System Uptime"*)
                        save_recent "System Uptime" "--uptime"
                        "$DAEMON_SCRIPT" --uptime & disown
                        exit 0
                        ;;
                    *"Pomodoro"*)
                        while true; do
                            last_pomo="1500:300"
                            [[ -f "$POMO_STATE" ]] && last_pomo=$(<"$POMO_STATE")
                            read -r lw_sec lb_sec <<< "$(parse_pomodoro "$last_pomo")"
                            p_opts=(
                                "  Back"
                                "󰐊  Start Last ($(fmt_t "$lw_sec") Work / $(fmt_t "$lb_sec") Break)"
                                "󰒓  Set in Minutes"
                                "󰒓  Set in Seconds"
                            )
                            pchoice=$(printf '%s\n' "${p_opts[@]}" | "${ROFI_SUB[@]}" -p "Pomodoro") || break
                            [[ "$pchoice" == "  Back" ]] && break
                            
                            if [[ "$pchoice" == *"Start Last"* ]]; then
                                save_recent "Pomodoro ($(fmt_t "$lw_sec") Work / $(fmt_t "$lb_sec") Break)" "--pomodoro $lw_sec $lb_sec"
                                "$DAEMON_SCRIPT" --pomodoro "$lw_sec" "$lb_sec" & disown
                                exit 0
                            elif [[ "$pchoice" == *"Minutes"* ]]; then
                                w=$(rofi -dmenu -i -p "Work (Mins)" -location 3 -theme-str "$PROMPT_STYLE") || continue
                                w=${w//[!0-9]/}; [[ -z "$w" ]] && continue
                                b=$(rofi -dmenu -i -p "Break (Mins)" -location 3 -theme-str "$PROMPT_STYLE") || continue
                                b=${b//[!0-9]/}; [[ -z "$b" ]] && b=0
                                echo "$((w*60)):$((b*60))" > "$POMO_STATE"
                                save_recent "Pomodoro (${w}m / ${b}m)" "--pomodoro $((w*60)) $((b*60))"
                                "$DAEMON_SCRIPT" --pomodoro "$((w*60))" "$((b*60))" & disown
                                exit 0
                            elif [[ "$pchoice" == *"Seconds"* ]]; then
                                w=$(rofi -dmenu -i -p "Work (Secs)" -location 3 -theme-str "$PROMPT_STYLE") || continue
                                w=${w//[!0-9]/}; [[ -z "$w" ]] && continue
                                b=$(rofi -dmenu -i -p "Break (Secs)" -location 3 -theme-str "$PROMPT_STYLE") || continue
                                b=${b//[!0-9]/}; [[ -z "$b" ]] && b=0
                                echo "$w:$b" > "$POMO_STATE"
                                save_recent "Pomodoro (${w}s / ${b}s)" "--pomodoro $w $b"
                                "$DAEMON_SCRIPT" --pomodoro "$w" "$b" & disown
                                exit 0
                            fi
                        done
                        ;;
                    *"Stopwatch"*)
                        save_recent "Stopwatch" "--stopwatch"
                        "$DAEMON_SCRIPT" --stopwatch & disown
                        exit 0
                        ;;
                esac
            done
            continue
            ;;

        '󰋊  Disk Usage')
            while true; do
                st_opts=(
                    "  Back"
                    "󰋊  Root Partition (/)"
                    "󰆼  Solid State Drives (SSD)"
                    "󰋊  Hard Disk Drives (HDD)"
                )
                stchoice=$(printf '%s\n' "${st_opts[@]}" | "${ROFI_SUB[@]}" -p "Storage Type") || break
                [[ "$stchoice" == "  Back" ]] && break

                if [[ "$stchoice" == *"Root Partition"* ]]; then
                    save_recent "Disk Space (Root)" "--disk"
                    "$DAEMON_SCRIPT" --disk & disown
                    exit 0
                    
                elif [[ "$stchoice" == *"Solid State Drives"* ]]; then
                    while true; do
                        declare -a ssd_opts=("  Back")
                        while IFS=$'\t' read -r name model rota; do
                            [[ "$name" =~ ^(loop|sr|ram|dm|fd) ]] && continue
                            if [[ "$rota" == "0" ]]; then
                                ssd_opts+=("󰆼  $name (${model:-Unknown})")
                            fi
                        done < <(lsblk -d -n -o NAME,MODEL,ROTA | awk '{ r=$NF; n=$1; $1=""; $NF=""; sub(/^[ \t]+/, ""); sub(/[ \t]+$/, ""); print n "\t" $0 "\t" r }')

                        [[ ${#ssd_opts[@]} -eq 1 ]] && ssd_opts+=("󰜺  No SSDs found")
                        
                        dchoice=$(printf '%s\n' "${ssd_opts[@]}" | "${ROFI_SUB[@]}" -p "Select SSD") || break
                        [[ "$dchoice" == "  Back" ]] && break
                        [[ "$dchoice" == *"No SSDs"* ]] && continue
                        
                        dev_name=$(echo "$dchoice" | awk '{print $2}')
                        while true; do
                            rw_opts=("  Back" "󰑍  Live Read" "󰏫  Live Write" "  Temperature")
                            rwchoice=$(printf '%s\n' "${rw_opts[@]}" | "${ROFI_SUB[@]}" -p "/dev/$dev_name") || break
                            [[ "$rwchoice" == "  Back" ]] && break
                            
                            if [[ "$rwchoice" == *"Read"* ]]; then
                                save_recent "Disk Read ($dev_name)" "--disk-read $dev_name"
                                "$DAEMON_SCRIPT" --disk-read "$dev_name" & disown
                                exit 0
                            elif [[ "$rwchoice" == *"Write"* ]]; then
                                save_recent "Disk Write ($dev_name)" "--disk-write $dev_name"
                                "$DAEMON_SCRIPT" --disk-write "$dev_name" & disown
                                exit 0
                            elif [[ "$rwchoice" == *"Temperature"* ]]; then
                                save_recent "Disk Temp ($dev_name)" "--disk-temp $dev_name"
                                "$DAEMON_SCRIPT" --disk-temp "$dev_name" & disown
                                exit 0
                            fi
                        done
                    done

                elif [[ "$stchoice" == *"Hard Disk Drives"* ]]; then
                    while true; do
                        declare -a hdd_opts=("  Back")
                        while IFS=$'\t' read -r name model rota; do
                            [[ "$name" =~ ^(loop|sr|ram|dm|fd) ]] && continue
                            if [[ "$rota" == "1" ]]; then
                                hdd_opts+=("󰋊  $name (${model:-Unknown})")
                            fi
                        done < <(lsblk -d -n -o NAME,MODEL,ROTA | awk '{ r=$NF; n=$1; $1=""; $NF=""; sub(/^[ \t]+/, ""); sub(/[ \t]+$/, ""); print n "\t" $0 "\t" r }')

                        [[ ${#hdd_opts[@]} -eq 1 ]] && hdd_opts+=("󰜺  No HDDs found")
                        
                        dchoice=$(printf '%s\n' "${hdd_opts[@]}" | "${ROFI_SUB[@]}" -p "Select HDD") || break
                        [[ "$dchoice" == "  Back" ]] && break
                        [[ "$dchoice" == *"No HDDs"* ]] && continue
                        
                        dev_name=$(echo "$dchoice" | awk '{print $2}')
                        while true; do
                            rw_opts=("  Back" "󰑍  Live Read" "󰏫  Live Write" "  Temperature")
                            rwchoice=$(printf '%s\n' "${rw_opts[@]}" | "${ROFI_SUB[@]}" -p "/dev/$dev_name") || break
                            [[ "$rwchoice" == "  Back" ]] && break
                            
                            if [[ "$rwchoice" == *"Read"* ]]; then
                                save_recent "Disk Read ($dev_name)" "--disk-read $dev_name"
                                "$DAEMON_SCRIPT" --disk-read "$dev_name" & disown
                                exit 0
                            elif [[ "$rwchoice" == *"Write"* ]]; then
                                save_recent "Disk Write ($dev_name)" "--disk-write $dev_name"
                                "$DAEMON_SCRIPT" --disk-write "$dev_name" & disown
                                exit 0
                            elif [[ "$rwchoice" == *"Temperature"* ]]; then
                                save_recent "Disk Temp ($dev_name)" "--disk-temp $dev_name"
                                "$DAEMON_SCRIPT" --disk-temp "$dev_name" & disown
                                exit 0
                            fi
                        done
                    done
                fi
            done
            continue
            ;;

        '  CPU')
            while true; do
                cpu_opts=(
                    "  Back"
                    "󱐋  CPU Power (Watts)"
                    "  CPU Usage"
                    "  CPU Temp"
                )
                cpuchoice=$(printf '%s\n' "${cpu_opts[@]}" | "${ROFI_SUB[@]}" -p "CPU") || break
                [[ "$cpuchoice" == "  Back" ]] && break
                
                if [[ "$cpuchoice" == *"CPU Power"* ]]; then
                    save_recent "CPU Power (Watts)" "--cpu-power"
                    "$DAEMON_SCRIPT" --cpu-power & disown
                    exit 0
                elif [[ "$cpuchoice" == *"CPU Usage"* ]]; then
                    save_recent "CPU Usage" "--cpu"
                    "$DAEMON_SCRIPT" --cpu & disown
                    exit 0
                elif [[ "$cpuchoice" == *"CPU Temp"* ]]; then
                    save_recent "CPU Temp" "--temp"
                    "$DAEMON_SCRIPT" --temp & disown
                    exit 0
                fi
            done
            continue
            ;;

        '󰘚  Memory (RAM)')
            while true; do
                m_opts=("  Back" "󰘚  System RAM Usage" "󰘚  RAM Temperature" "󰘚  ZRAM Usage")
                mchoice=$(printf '%s\n' "${m_opts[@]}" | "${ROFI_SUB[@]}" -p "Memory") || break
                [[ "$mchoice" == "  Back" ]] && break

                if [[ "$mchoice" == *"System RAM"* ]]; then
                    save_recent "RAM Usage" "--ram"
                    "$DAEMON_SCRIPT" --ram & disown
                    exit 0
                elif [[ "$mchoice" == *"Temperature"* ]]; then
                    save_recent "RAM Temp" "--ram-temp"
                    "$DAEMON_SCRIPT" --ram-temp & disown
                    exit 0
                elif [[ "$mchoice" == *"ZRAM"* ]]; then
                    save_recent "ZRAM Usage" "--zram"
                    "$DAEMON_SCRIPT" --zram & disown
                    exit 0
                fi
            done
            continue
            ;;

        '󰁹  Battery')
            while true; do
                b_opts=(
                    "  Back"
                    "󰁹  Power Draw Only"
                    "󰁹  Percent Only"
                    "󰁹  Time Remaining Only"
                    "󰁹  Standard HUD"
                )
                bchoice=$(printf '%s\n' "${b_opts[@]}" | "${ROFI_SUB[@]}" -p "Battery") || break
                [[ "$bchoice" == "  Back" ]] && break
                
                if [[ "$bchoice" == *"Standard HUD"* ]]; then
                    save_recent "Battery HUD" "--battery"
                    "$DAEMON_SCRIPT" --battery & disown
                    exit 0
                elif [[ "$bchoice" == *"Percent Only"* ]]; then
                    save_recent "Battery Percent" "--battery-percent"
                    "$DAEMON_SCRIPT" --battery-percent & disown
                    exit 0
                elif [[ "$bchoice" == *"Power Draw Only"* ]]; then
                    save_recent "Battery Power Draw" "--battery-watts"
                    "$DAEMON_SCRIPT" --battery-watts & disown
                    exit 0
                elif [[ "$bchoice" == *"Time Remaining Only"* ]]; then
                    save_recent "Battery Time" "--battery-time"
                    "$DAEMON_SCRIPT" --battery-time & disown
                    exit 0
                fi
            done
            continue
            ;;

        '󰈀  Network Speed')
            save_recent "Network Speed" "--network"
            "$DAEMON_SCRIPT" --network & disown
            exit 0
            ;;

        '󰽽  Workspace')
            save_recent "Workspace" "--workspace"
            "$DAEMON_SCRIPT" --workspace & disown
            exit 0
            ;;

        '󰸉  Edit')
            foot --app-id=dusky_tui python ~/user_scripts/dusky_tui/python/main/main.py ~/user_scripts/mako_osd/dusky_glance/tui_glance_mako.py & disown
            exit 0
            ;;

        '󰜺  Stop / Clear')
            "$DAEMON_SCRIPT" --stop & disown
            exit 0
            ;;
    esac
done
