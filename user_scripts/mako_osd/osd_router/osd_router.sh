#!/usr/bin/env bash
# Hyprland Native OSD Router - Stateless IPC Edition
# Optimized for Bash 5.3.9+ and Wayland/UWSM environments

SYNC_ID="sys-osd"

# Core notification wrapper
notify() {
    local icon="$1"
    local title="$2"
    local val="$3"
    
    if [[ -n "$val" ]]; then
        notify-send -a "OSD" -h string:x-canonical-private-synchronous:"$SYNC_ID" -h int:value:"$val" -i "$icon" "$title"
    else
        notify-send -a "OSD" -h string:x-canonical-private-synchronous:"$SYNC_ID" -i "$icon" "$title"
    fi
}

# Atomic write to entirely eliminate torn reads by the async worker
atomic_write() {
    local file="$1"
    local data="$2"
    echo "$data" > "${file}.tmp"
    mv "${file}.tmp" "$file"
}

main() {
    local action="$1"
    local step="${2:-5}"

    case "$action" in
        --vol-up|--vol-down)
            exec {lock_fd}> "${XDG_RUNTIME_DIR:-/tmp}/osd_audio.lock"
            flock -x "$lock_fd"

            local icon title vol
            if [[ "$action" == "--vol-up" ]]; then
                wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ "${step}%+"
                icon="audio-volume-high"
            else
                wpctl set-volume @DEFAULT_AUDIO_SINK@ "${step}%-"
                icon="audio-volume-low"
            fi
            
            vol=$(wpctl get-volume @DEFAULT_AUDIO_SINK@ | awk '{print int($2 * 100 + 0.5)}')
            title="Volume: ${vol}%"
            
            # Write target state atomically while holding hardware lock
            atomic_write "${XDG_RUNTIME_DIR:-/tmp}/osd_audio_state.txt" "$icon|$title|$vol"
            exec {lock_fd}>&-
            
            # Asynchronous Single Worker Loop
            (
                flock -n 9 || exit 0
                while true; do
                    IFS='|' read -r c_icon c_title c_vol < "${XDG_RUNTIME_DIR:-/tmp}/osd_audio_state.txt"
                    [[ -z "$c_title" ]] && break 
                    
                    notify "$c_icon" "$c_title" "$c_vol"
                    
                    IFS='|' read -r n_icon n_title n_vol < "${XDG_RUNTIME_DIR:-/tmp}/osd_audio_state.txt"
                    if [[ "$c_vol" == "$n_vol" && "$c_icon" == "$n_icon" && "$c_title" == "$n_title" ]]; then
                        break # State caught up, exit worker cleanly
                    fi
                done
            ) 9>> "${XDG_RUNTIME_DIR:-/tmp}/osd_audio_ui.lock" &
            ;;

        --vol-mute)
            exec {lock_fd}> "${XDG_RUNTIME_DIR:-/tmp}/osd_audio.lock"
            flock -x "$lock_fd"

            wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle
            
            local icon title vol
            if wpctl get-volume @DEFAULT_AUDIO_SINK@ | grep -q "MUTED"; then
                icon="audio-volume-muted"
                title="Audio Muted"
                vol=""
            else
                icon="audio-volume-high"
                vol=$(wpctl get-volume @DEFAULT_AUDIO_SINK@ | awk '{print int($2 * 100 + 0.5)}')
                title="Audio Unmuted"
            fi
            
            atomic_write "${XDG_RUNTIME_DIR:-/tmp}/osd_audio_state.txt" "$icon|$title|$vol"
            exec {lock_fd}>&-

            (
                flock -n 9 || exit 0
                while true; do
                    IFS='|' read -r c_icon c_title c_vol < "${XDG_RUNTIME_DIR:-/tmp}/osd_audio_state.txt"
                    [[ -z "$c_title" ]] && break
                    
                    notify "$c_icon" "$c_title" "$c_vol"
                    
                    IFS='|' read -r n_icon n_title n_vol < "${XDG_RUNTIME_DIR:-/tmp}/osd_audio_state.txt"
                    if [[ "$c_vol" == "$n_vol" && "$c_icon" == "$n_icon" && "$c_title" == "$n_title" ]]; then
                        break
                    fi
                done
            ) 9>> "${XDG_RUNTIME_DIR:-/tmp}/osd_audio_ui.lock" &
            ;;

        --mic-mute)
            wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle
            if wpctl get-volume @DEFAULT_AUDIO_SOURCE@ | grep -q "MUTED"; then
                notify "microphone-sensitivity-muted" "Microphone Muted" ""
            else
                notify "audio-input-microphone" "Microphone Live" ""
            fi
            ;;

        --bright-up|--bright-down)
            exec {lock_fd}> "${XDG_RUNTIME_DIR:-/tmp}/osd_display.lock"
            flock -x "$lock_fd"

            if [[ "$action" == "--bright-up" ]]; then
                brightnessctl set "${step}%+" -q
            else
                brightnessctl set "${step}%-" -q
            fi
            
            local icon="gpm-brightness-lcd" title bright
            bright=$(brightnessctl -m | awk -F, '{print int($4 + 0.5)}')
            title="Brightness: ${bright}%"
            
            atomic_write "${XDG_RUNTIME_DIR:-/tmp}/osd_display_state.txt" "$icon|$title|$bright"
            exec {lock_fd}>&-
            
            (
                flock -n 9 || exit 0
                while true; do
                    IFS='|' read -r c_icon c_title c_bright < "${XDG_RUNTIME_DIR:-/tmp}/osd_display_state.txt"
                    [[ -z "$c_title" ]] && break
                    
                    notify "$c_icon" "$c_title" "$c_bright"
                    
                    IFS='|' read -r n_icon n_title n_bright < "${XDG_RUNTIME_DIR:-/tmp}/osd_display_state.txt"
                    if [[ "$c_bright" == "$n_bright" && "$c_icon" == "$n_icon" && "$c_title" == "$n_title" ]]; then
                        break
                    fi
                done
            ) 9>> "${XDG_RUNTIME_DIR:-/tmp}/osd_display_ui.lock" &
            ;;

        --kbd-bright-up|--kbd-bright-down)
            exec {lock_fd}> "${XDG_RUNTIME_DIR:-/tmp}/osd_kbd.lock"
            flock -x "$lock_fd"

            local kbd_dev
            kbd_dev=$(brightnessctl -l | awk -F"'" '/kbd_backlight/ {print $2; exit}')

            if [[ -z "$kbd_dev" ]]; then
                notify "dialog-error" "No Kbd Backlight Found" ""
                exec {lock_fd}>&-
                exit 1
            fi

            if [[ "$action" == "--kbd-bright-up" ]]; then
                brightnessctl --device="$kbd_dev" set "${step}%+" -q
            else
                brightnessctl --device="$kbd_dev" set "${step}%-" -q
            fi

            local icon="keyboard-brightness" title kbd_bright
            kbd_bright=$(brightnessctl --device="$kbd_dev" -m 2>/dev/null | awk -F, '{print int($4 + 0.5)}')
            [[ -z "$kbd_bright" ]] && kbd_bright=0
            title="Kbd Brightness: ${kbd_bright}%"

            atomic_write "${XDG_RUNTIME_DIR:-/tmp}/osd_kbd_state.txt" "$icon|$title|$kbd_bright"
            exec {lock_fd}>&-

            (
                flock -n 9 || exit 0
                while true; do
                    IFS='|' read -r c_icon c_title c_bright < "${XDG_RUNTIME_DIR:-/tmp}/osd_kbd_state.txt"
                    [[ -z "$c_title" ]] && break
                    
                    notify "$c_icon" "$c_title" "$c_bright"
                    
                    IFS='|' read -r n_icon n_title n_bright < "${XDG_RUNTIME_DIR:-/tmp}/osd_kbd_state.txt"
                    if [[ "$c_bright" == "$n_bright" && "$c_icon" == "$n_icon" && "$c_title" == "$n_title" ]]; then
                        break
                    fi
                done
            ) 9>> "${XDG_RUNTIME_DIR:-/tmp}/osd_kbd_ui.lock" &
            ;;

        --kbd-bright-show)
            local kbd_dev
            kbd_dev=$(brightnessctl -l | awk -F"'" '/kbd_backlight/ {print $2; exit}')
            
            if [[ -z "$kbd_dev" ]]; then
                exit 0
            fi

            local kbd_bright
            kbd_bright=$(brightnessctl --device="$kbd_dev" -m 2>/dev/null | awk -F, '{print int($4 + 0.5)}')
            [[ -z "$kbd_bright" ]] && kbd_bright=0

            notify "keyboard-brightness" "Kbd Brightness: ${kbd_bright}%" "$kbd_bright"
            ;;

        --play-pause|--next|--prev|--stop)
            local old_meta old_status
            old_meta=$(playerctl metadata --format "{{ artist }} - {{ title }}" 2>/dev/null)
            old_status=$(playerctl status 2>/dev/null)

            case "$action" in
                --play-pause) playerctl play-pause ;;
                --next)       playerctl next ;;
                --prev)       playerctl previous ;;
                --stop)       playerctl stop ;;
            esac
            
            local status metadata
            # 100 iterations * 10ms = 1.0 second max timeout for network streams
            for ((i=0; i<100; i++)); do
                status=$(playerctl status 2>/dev/null)
                metadata=$(playerctl metadata --format "{{ artist }} - {{ title }}" 2>/dev/null)
                
                # Strict state-transition validation
                case "$action" in
                    --play-pause)
                        [[ "$status" != "$old_status" && -n "$status" ]] && break
                        ;;
                    --next|--prev)
                        [[ "$metadata" != "$old_meta" ]] && break
                        ;;
                    --stop)
                        [[ "$status" == "Stopped" || -z "$status" ]] && break
                        ;;
                esac
                
                read -r -t 0.01 <> <(:)
            done
            
            [[ -z "$metadata" || "$metadata" == " - " ]] && metadata="Unknown Track"

            if [[ "$status" == "Playing" ]]; then
                icon="media-playback-start"
                title="$metadata"
            elif [[ "$status" == "Paused" ]]; then
                icon="media-playback-pause"
                title="Paused: $metadata"
            elif [[ "$status" == "Stopped" || -z "$status" ]]; then
                icon="media-playback-stop"
                title="Stopped"
            else
                icon="dialog-error"
                title="No Active Player"
            fi
            
            notify "$icon" "$title" ""
            ;;

        *)
            echo "Usage: $0 {--vol-up|--vol-down|--vol-mute|--mic-mute|--bright-up|--bright-down|--kbd-bright-up|--kbd-bright-down|--kbd-bright-show|--play-pause|--next|--prev|--stop} [step_value]"
            exit 1
            ;;
    esac
}

main "$@"
