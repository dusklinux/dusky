#!/usr/bin/env bash
# ==============================================================================
# DUSKY GLANCE - ROFI FRONTEND
# ==============================================================================

set -euo pipefail

# Hardcoded absolute path to the daemon
DAEMON_SCRIPT="$HOME/user_scripts/mako_osd/dusky_glance/dusky_glance_daemon.sh"

declare -agr ROFI_CMD=(rofi -dmenu -i -no-custom -theme-str 'window {width: 20%;} listview {lines: 10;}')

declare -agr MENU_OPTIONS=(
    '🍅  Pomodoro (25m)'
    '⏳  Custom Timer'
    '⏱️  Stopwatch'
    '🕒  Live Clock'
    '💻  CPU Usage'
    '🧠  Memory (RAM)'
    '🌡️  CPU Temp'
    '🔋  Battery / Power'
    '🌐  Network Speed'
    '🛑  Stop / Clear'
)

choice=$(printf '%s\n' "${MENU_OPTIONS[@]}" | "${ROFI_CMD[@]}" -p "Glance") || exit 0

case "$choice" in
    '🍅  Pomodoro (25m)') "$DAEMON_SCRIPT" --pomodoro 1500 & disown ;;
    '⏳  Custom Timer')
        mins=$(rofi -dmenu -i -p "Minutes" -theme-str 'window {width: 15%;} listview {lines: 0;}') || exit 0
        if [[ "$mins" =~ ^[0-9]{1,5}$ ]]; then
            clean_mins=$(( 10#$mins ))
            if (( clean_mins > 0 && clean_mins <= 1440 )); then 
                "$DAEMON_SCRIPT" --timer "$(( clean_mins * 60 ))" & disown
            else
                notify-send -u low "Invalid time entered."
            fi
        else
            notify-send -u low "Invalid format."
        fi
        ;;
    '⏱️  Stopwatch')      "$DAEMON_SCRIPT" --stopwatch & disown ;;
    '🕒  Live Clock')     "$DAEMON_SCRIPT" --clock & disown ;;
    '💻  CPU Usage')      "$DAEMON_SCRIPT" --cpu & disown ;;
    '🧠  Memory (RAM)')   "$DAEMON_SCRIPT" --ram & disown ;;
    '🌡️  CPU Temp')       "$DAEMON_SCRIPT" --temp & disown ;;
    '🔋  Battery / Power')"$DAEMON_SCRIPT" --battery & disown ;;
    '🌐  Network Speed')  "$DAEMON_SCRIPT" --network & disown ;;
    '🛑  Stop / Clear')   "$DAEMON_SCRIPT" --stop & disown ;;
esac
