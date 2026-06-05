#!/usr/bin/env bash
# ==============================================================================
# ARCH LINUX :: WAYLAND :: ROFI DUSKY RECORDER
# ==============================================================================
# Description: Interactive Rofi interface for gpu-screen-recorder.
#              - State-aware Start/Stop/Replay controls
#              - Full Screen vs Region selection
#              - Quick settings editor (FPS, Cursor, Audio, Indicator)
#              - Async blinking Mako red-dot indicator
# ==============================================================================

set -Eeuo pipefail

# --- CONFIGURATION ---
readonly CFG="$HOME/.config/dusky_recorder/config.conf"
readonly ROFI_THEME_STR='window { width: 450px; } listview { lines: 8; }'
readonly INDICATOR_TMP="/tmp/dusky_recorder_notif_id"
readonly INDICATOR_PID="/tmp/dusky_recorder_daemon.pid"

# Ensure config exists and load it
[[ -f "$CFG" ]] && source "$CFG"

# Fallbacks
fps="${fps:-60}"
cursor="${cursor:-yes}"
audio="${audio:-default_output}"
container="${container:-mp4}"
output_dir="${output_dir:-$HOME/Videos}"
output_dir="${output_dir/#\~/$HOME}"
replay_buffer="${replay_buffer:-0}"
show_indicator="${show_indicator:-yes}"

# --- HELPERS ---
run_menu() {
    local prompt="$1"
    shift
    local options=("$@")
    printf '%s\n' "${options[@]}" | rofi -dmenu -i -p "$prompt" -theme-str "$ROFI_THEME_STR" -format s
}

update_config() {
    local key="$1"
    local value="$2"
    if grep -q "^${key}=" "$CFG"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$CFG"
    else
        echo "${key}=${value}" >> "$CFG"
    fi
    # Update current session variables
    export "$key"="$value"
}

manage_indicator() {
    local action="$1"
    
    if [[ "$action" == "start" ]]; then
        # Check if the user wants the indicator enabled
        [[ "$show_indicator" != "yes" ]] && return 0

        # Launch an async subshell to handle the blinking loop
        (
            local notif_id
            # Send initial notification and grab its ID
            notif_id=$(notify-send -a "dusky-recorder" -p "" "")
            echo "$notif_id" > "$INDICATOR_TMP"
            
            # Blinker daemon loop
            local visible=true
            while true; do
                sleep 1
                if $visible; then
                    # Replace text with a space to "hide" the dot
                    notify-send -a "dusky-recorder" -r "$notif_id" " " ""
                    visible=false
                else
                    # Bring the dot back
                    notify-send -a "dusky-recorder" -r "$notif_id" "" ""
                    visible=true
                fi
            done
        ) & 
        # Save the PID of the subshell so we can kill it later
        echo $! > "$INDICATOR_PID"
        
    elif [[ "$action" == "stop" ]]; then
        # 1. Kill the blink daemon (if it exists)
        if [[ -f "$INDICATOR_PID" ]]; then
            kill "$(cat "$INDICATOR_PID")" 2>/dev/null || true
            rm -f "$INDICATOR_PID"
        fi
        
        # 2. Dismiss the notification from Mako
        if [[ -f "$INDICATOR_TMP" ]]; then
            local notif_id
            notif_id=$(cat "$INDICATOR_TMP")
            makoctl dismiss -n "$notif_id" 2>/dev/null || true
            rm -f "$INDICATOR_TMP"
        fi
    fi
}

# --- RECORDING LOGIC ---
stop_recording() {
    local pids
    if pids=$(pidof gpu-screen-recorder || true); then
        if [[ -n "$pids" ]]; then
            for pid in $pids; do
                kill -SIGINT "$pid"
            done
            notify-send -u normal -i media-playback-stop 'Dusky Recorder' '  Recording stopped'
            manage_indicator "stop"
        fi
    fi
}

save_replay() {
    local pids
    if pids=$(pidof gpu-screen-recorder || true); then
        if [[ -n "$pids" ]]; then
            for pid in $pids; do
                kill -SIGUSR1 "$pid"
            done
            notify-send -u normal -i media-record 'Dusky Replay' '  Replay buffer saved'
        fi
    fi
}

start_recording() {
    local target_mode="$1" # "screen" or "region"
    
    # 1. Wayland Region Selection (if requested)
    local region_coords=""
    if [[ "$target_mode" == "region" ]]; then
        # 500ms buffer allows Hyprland to release Rofi's exclusive keybind grab
        sleep 0.5 
        if ! region_coords=$(slurp -f "%wx%h+%x+%y" 2>/dev/null); then
            notify-send -u critical 'Dusky Recorder Error' 'Region selection cancelled'
            exit 1
        fi
        [[ -z "$region_coords" ]] && exit 1
    fi

    # 2. Guarantee Output Directory
    mkdir -p "$output_dir"

    # 3. Argument Array Construction
    local -a args=(
        gpu-screen-recorder
        -w "$target_mode"
        -c "$container"
        -f "$fps"
    )

    # Apply Region
    [[ "$target_mode" == "region" && -n "$region_coords" ]] && args+=(-region "$region_coords")

    # Apply Config Modifiers
    [[ -n "$audio" && "$audio" != "none" ]] && args+=(-a "$audio")
    [[ "$cursor" == "no" ]] && args+=(-cursor "no")
    
    # Optional hardware/backend variables from config
    [[ -n "${codec:-}" ]] && args+=(-k "$codec")
    [[ -n "${quality:-}" ]] && args+=(-q "$quality")
    [[ -n "${encoder:-}" ]] && args+=(-encoder "$encoder")
    [[ -n "${bitrate_mode:-}" ]] && args+=(-bm "$bitrate_mode")
    [[ -n "${frame_mode:-}" ]] && args+=(-fm "$frame_mode")

    # 4. Routing Output Modes
    local OUT=""
    if [[ -n "$replay_buffer" && "$replay_buffer" -gt 0 ]]; then
        args+=(-r "$replay_buffer")
        OUT="$output_dir"
    else
        OUT="${output_dir}/Video_$(date +%Y-%m-%d_%H-%M-%S).${container}"
    fi
    args+=(-o "$OUT")

    # 5. Execution
    "${args[@]}" > /tmp/gsr.log 2>&1 &
    local new_pid=$!

    sleep 0.5
    if ! kill -0 "$new_pid" 2>/dev/null; then
        notify-send -u critical 'Dusky Recorder Error' "Failed to start. Check /tmp/gsr.log"
        exit 1
    else
        if [[ -n "$replay_buffer" && "$replay_buffer" -gt 0 ]]; then
            notify-send -u normal -i media-record 'Dusky Recorder' '  Replay daemon started'
        else
            notify-send -u normal -i media-record 'Dusky Recorder' '  Recording started'
        fi
        manage_indicator "start"
    fi
}

# --- SUBMENUS ---
settings_menu() {
    while true; do
        local -a opts=(
            "  Back"
            "󰣖  FPS         [${fps}]"
            "󰇀  Cursor      [${cursor}]"
            "󰎆  Audio       [${audio}]"
            "󰂚  Indicator   [${show_indicator}]"
            "  Replay Buf  [${replay_buffer}s]"
        )
        local choice
        choice=$(run_menu "  Quick Settings" "${opts[@]}") || return 0

        case "$choice" in
            "  Back"*) return 0 ;;
            "󰣖  FPS"*)
                local new_fps
                new_fps=$(run_menu "Select FPS" "30" "60" "120" "144") || continue
                [[ -n "$new_fps" ]] && { fps="$new_fps"; update_config "fps" "$fps"; }
                ;;
            "󰇀  Cursor"*)
                local new_cursor
                new_cursor=$(run_menu "Record Cursor?" "yes" "no") || continue
                [[ -n "$new_cursor" ]] && { cursor="$new_cursor"; update_config "cursor" "$cursor"; }
                ;;
            "󰎆  Audio"*)
                local new_audio
                new_audio=$(run_menu "Select Audio Source" "default_output" "default_input" "default_output|default_input" "none") || continue
                [[ -n "$new_audio" ]] && { audio="$new_audio"; update_config "audio" "$audio"; }
                ;;
            "󰂚  Indicator"*)
                local new_ind
                new_ind=$(run_menu "Show Red Dot Indicator?" "yes" "no") || continue
                [[ -n "$new_ind" ]] && { show_indicator="$new_ind"; update_config "show_indicator" "$show_indicator"; }
                ;;
            "  Replay"*)
                local new_buf
                new_buf=$(run_menu "Replay Buffer (0 to disable)" "0" "30" "60" "120" "300") || continue
                [[ -n "$new_buf" ]] && { replay_buffer="$new_buf"; update_config "replay_buffer" "$replay_buffer"; }
                ;;
        esac
    done
}

# --- MAIN LOOP ---
main() {
    # Check current state
    local is_running=false
    local is_replay=false
    local pids
    
    if pids=$(pidof gpu-screen-recorder || true); then
        if [[ -n "$pids" ]]; then
            is_running=true
            # Check if running in replay mode
            if grep -zqxa -- '-r' "/proc/$(echo "$pids" | awk '{print $1}')/cmdline" 2>/dev/null; then
                is_replay=true
            fi
        fi
    fi

    # Build Main Menu based on state
    local -a main_opts=()
    if $is_running; then
        $is_replay && main_opts+=("  Save Replay Buffer")
        main_opts+=("  Stop Recording")
        main_opts+=("  Cancel")
    else
        main_opts+=("  Record Full Screen")
        main_opts+=("  Record Region")
        main_opts+=("  Quick Settings")
        main_opts+=("  Cancel")
    fi

    local choice
    choice=$(run_menu "Dusky Recorder" "${main_opts[@]}") || exit 0

    case "$choice" in
        "  Stop"*) stop_recording ;;
        "  Save"*) save_replay ;;
        "  Record"*) start_recording "screen" ;;
        "  Record"*) start_recording "region" ;;
        "  Quick"*) settings_menu; main ;; # Re-run main after returning from settings
        "  Cancel"*) exit 0 ;;
    esac
}

main
