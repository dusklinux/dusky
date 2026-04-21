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

main() {
    local action="$1"
    local step="${2:-5}"

    case "$action" in
        --vol-up|--vol-down)
            exec {lock_fd}> "${XDG_RUNTIME_DIR:-/tmp}/osd_audio.lock"
            flock -x "$lock_fd"

            local icon
            if [[ "$action" == "--vol-up" ]]; then
                wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ "${step}%+"
                icon="audio-volume-high"
            else
                wpctl set-volume @DEFAULT_AUDIO_SINK@ "${step}%-"
                icon="audio-volume-low"
            fi
            
            local vol
            vol=$(wpctl get-volume @DEFAULT_AUDIO_SINK@ | awk '{print int($2 * 100 + 0.5)}')
            notify "$icon" "Volume: ${vol}%" "$vol"
            
            exec {lock_fd}>&-
            ;;

        --vol-mute)
            exec {lock_fd}> "${XDG_RUNTIME_DIR:-/tmp}/osd_audio.lock"
            flock -x "$lock_fd"

            wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle
            if wpctl get-volume @DEFAULT_AUDIO_SINK@ | grep -q "MUTED"; then
                notify "audio-volume-muted" "Audio Muted" ""
            else
                local vol
                vol=$(wpctl get-volume @DEFAULT_AUDIO_SINK@ | awk '{print int($2 * 100 + 0.5)}')
                notify "audio-volume-high" "Audio Unmuted" "$vol"
            fi
            
            exec {lock_fd}>&-
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
            
            local bright
            bright=$(brightnessctl -m | awk -F, '{print int($4 + 0.5)}')
            notify "display-brightness" "Brightness: ${bright}%" "$bright"
            
            exec {lock_fd}>&-
            ;;

        --kbd-bright-up|--kbd-bright-down)
            local kbd_dev
            kbd_dev=$(brightnessctl -l | awk -F"'" '/kbd_backlight/ {print $2; exit}')

            if [[ -z "$kbd_dev" ]]; then
                notify "dialog-error" "No Kbd Backlight Found" ""
                exit 1
            fi

            if [[ "$action" == "--kbd-bright-up" ]]; then
                brightnessctl --device="$kbd_dev" set "${step}%+" -q
            else
                brightnessctl --device="$kbd_dev" set "${step}%-" -q
            fi

            local kbd_bright
            kbd_bright=$(brightnessctl --device="$kbd_dev" -m 2>/dev/null | awk -F, '{print int($4 + 0.5)}')
            [[ -z "$kbd_bright" ]] && kbd_bright=0

            notify "keyboard-brightness" "Kbd Brightness: ${kbd_bright}%" "$kbd_bright"
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
            local old_meta
            old_meta=$(playerctl metadata --format "{{ artist }} - {{ title }}" 2>/dev/null)

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
                
                if [[ "$metadata" != "$old_meta" ]] || \
                   [[ "$action" == "--play-pause" && -n "$status" ]] || \
                   [[ "$action" == "--stop" && "$status" == "Stopped" ]]; then
                    break
                fi
                read -r -t 0.01 <> <(:)
            done
            
            [[ -z "$metadata" || "$metadata" == " - " ]] && metadata="Unknown Track"

            if [[ "$status" == "Playing" ]]; then
                icon="media-playback-start"
                title="$metadata"
            elif [[ "$status" == "Paused" ]]; then
                icon="media-playback-pause"
                title="Paused: $metadata"
            elif [[ "$status" == "Stopped" ]]; then
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
