#!/usr/bin/env bash
# Hyprland/Wayland OSD router. Hardware updates serialize per control;
# one asynchronous notifier per control coalesces rapid updates.
set -euo pipefail
export LC_ALL=C
SYNC_ID=sys-osd
RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$UID}

notify() {
    local icon=$1 title=$2 value=${3:-}
    local name=$icon candidate
    case $name in
        gpm-brightness-lcd|display-brightness-*) name=xfpm-brightness-lcd ;;
        keyboard-brightness) name=input-keyboard-brightness ;;
    esac
    # Direct files avoid an expensive theme lookup on every held-key repeat.
    # Papirus is shipped with the ISO; retain named icons on other installations.
    for candidate in /usr/share/icons/Papirus/{32x32,24x24}/{status,panel,actions,devices}/"$name.svg"; do
        if [[ -f $candidate ]]; then icon=$candidate; break; fi
    done
    local -a args=(-a OSD -h "string:x-canonical-private-synchronous:$SYNC_ID" -i "$icon")
    if [[ -n $value ]]; then
        # Notification progress is 0–100 even if external tools enabled amplification.
        (( value > 100 )) && value=100
        args+=(-h "int:value:$value")
    fi
    notify-send "${args[@]}" -- "$title"
}

lock_control() {
    exec {lock_fd}> "$RUNTIME_DIR/osd_$1.lock"
    flock -x "$lock_fd"
    state_file=$RUNTIME_DIR/osd_$1_state.txt
}

ui_worker() (
    local group=$1 snapshot latest worker_icon worker_title worker_value
    exec 9> "$RUNTIME_DIR/osd_${group}_ui.lock"
    flock -n 9 || exit 0
    exec {worker_fd}> "$RUNTIME_DIR/osd_$group.lock"
    while true; do
        flock -x "$worker_fd"
        snapshot=$(< "$state_file")
        flock -u "$worker_fd"
        IFS='|' read -r worker_icon worker_title worker_value <<< "$snapshot"
        notify "$worker_icon" "$worker_title" "$worker_value" || exit "$?"
        flock -x "$worker_fd"
        latest=$(< "$state_file")
        if [[ $snapshot == "$latest" ]]; then
            # Release the UI lock BEFORE allowing another writer to publish.
            # A writer can then always acquire a worker for its final update.
            exec 9>&-
            exit 0
        fi
        flock -u "$worker_fd"
    done
)

publish() {
    local group=$1
    # All readers and writers use the control lock: no temporary file or mv needed.
    printf '%s|%s|%s\n' "$icon" "$title" "$value" > "$state_file"
    exec {lock_fd}>&-
    ui_worker "$group" </dev/null >/dev/null &
}

# Parse brightnessctl's machine format, using raw values for consistent rounding.
parse_brightness() {
    local row=$1 _class _percent
    IFS=, read -r device _class current _percent maximum <<< "$row"
    [[ $current =~ ^[0-9]+$ && $maximum =~ ^[1-9][0-9]*$ ]] || {
        printf 'Invalid brightnessctl output: %s\n' "$row" >&2
        return 1
    }
    value=$(( (current * 100 + maximum / 2) / maximum ))
}

keyboard_device() {
    local devices
    devices=$(brightnessctl -m -l -c leds)
    while IFS= read -r row; do
        if [[ ${row%%,*} == *kbd_backlight* ]]; then
            parse_brightness "$row"
            return
        fi
    done <<< "$devices"
    return 1
}

get_dpms() {
    local monitors
    # The documented request socket avoids hyprctl's ~20 ms response wait on
    # every repeat. The instance and runtime paths come from this session.
    monitors=$(socat - "UNIX-CONNECT:$RUNTIME_DIR/hypr/${HYPRLAND_INSTANCE_SIGNATURE:?}/.socket.sock" <<< /monitors) || return
    # Active outputs only: disabled connectors must not trigger wake/off handling.
    dpms_off=0
    if [[ $monitors =~ dpmsStatus:[[:space:]]*(0|false) ]]; then dpms_off=1; fi
}

set_dpms() {
    local result
    result=$(hyprctl eval "hl.dispatch(hl.dsp.dpms({ action = \"$1\" }))")
    [[ $result == ok ]] || { printf 'DPMS failed: %s\n' "$result" >&2; return 1; }
}

media() {
    local action=$1 players player command old status metadata track old_track i
    local format=$'{{status}}\n{{mpris:trackid}}\n{{artist}} - {{title}}'
    players=$(playerctl -l) || { notify dialog-error 'No Active Player'; return 1; }
    player=${players%%$'\n'*}
    local -a ctl=(playerctl --player="$player")
    old=$("${ctl[@]}" metadata --format "$format")
    old_track=${old#*$'\n'}
    case $action in
        --prev) command=previous ;;
        *) command=${action#--} ;;
    esac
    "${ctl[@]}" "$command"
    for ((i=0; i<40; i++)); do
        if ! metadata=$("${ctl[@]}" metadata --format "$format"); then
            notify dialog-error 'Player Unavailable'
            return 1
        fi
        status=${metadata%%$'\n'*}
        track=${metadata#*$'\n'}
        metadata=${track#*$'\n'}
        case $action in
            --play-pause) [[ $status != "${old%%$'\n'*}" ]] && break ;;
            --next|--prev) [[ $track != "$old_track" ]] && break ;;
            --stop) [[ $status == Stopped ]] && break ;;
        esac
        (( i == 39 )) || sleep 0.025
    done
    [[ -n $metadata && $metadata != ' - ' ]] || metadata='Unknown Track'
    case $status in
        Playing) notify media-playback-start "$metadata" ;;
        Paused) notify media-playback-pause "Paused: $metadata" ;;
        Stopped) notify media-playback-stop Stopped ;;
        *) notify dialog-error 'Unknown Player State'; return 1 ;;
    esac
}

main() {
    local action=${1:-} step=${2:-5}
    if (( $# > 2 )) || [[ ! $step =~ ^[0-9]{1,3}$ ]] || (( 10#$step < 1 || 10#$step > 100 )); then
        printf 'Step must be an integer from 1 to 100; at most two arguments are accepted.\n' >&2
        return 1
    fi
    step=$((10#$step))
    local icon title value='' lock_fd state_file device current maximum row volume
    case $action in
        --vol-up|--vol-down|--vol-mute|--mic-mute)
            local group=audio
            local -a mixer=(pamixer)
            if [[ $action == --mic-mute ]]; then
                group=mic
                mixer+=(--default-source)
            fi
            lock_control "$group"
            case $action in
                --vol-up) mixer+=(--increase "$step") ;;
                --vol-down) mixer+=(--decrease "$step") ;;
                *) mixer+=(--toggle-mute) ;;
            esac
            volume=$("${mixer[@]}" --get-volume-human)
            if [[ $volume == muted ]]; then
                value=0
            elif [[ $volume =~ ^([0-9]+)%$ ]]; then
                value=$((10#${BASH_REMATCH[1]}))
            else
                printf 'Invalid pamixer output: %s\n' "$volume" >&2
                return 1
            fi
            icon=audio-volume-high
            title="Volume: $value%"
            if [[ $volume == muted ]]; then
                icon=audio-volume-muted title='Audio Muted' value=''
            elif [[ $action == --vol-mute ]]; then
                title='Audio Unmuted'
            fi
            if [[ $action == --mic-mute ]]; then
                value=''
                if [[ $volume == muted ]]; then
                    icon=microphone-sensitivity-muted title='Microphone Muted'
                else
                    icon=audio-input-microphone title='Microphone Live'
                fi
            fi
            publish "$group"
            ;;
        --bright-up|--bright-down)
            lock_control display
            local last_title='' _ignored floor woke=0 dpms_off
            if [[ -f $state_file ]]; then
                IFS='|' read -r _ignored last_title _ignored < "$state_file"
            fi
            row=$(brightnessctl -m -c backlight)
            parse_brightness "$row"
            floor=$(( (maximum + 99) / 100 ))
            icon=gpm-brightness-lcd
            if [[ $action == --bright-down ]]; then
                if (( current <= floor )); then
                    get_dpms
                    if (( dpms_off )); then
                        icon=display-brightness-off title='Screen Off' value=0
                    elif [[ $last_title == 'Swipe again to turn off' ]]; then
                        set_dpms disable
                        icon=display-brightness-off title='Screen Off' value=0
                    else
                        row=$(brightnessctl -m -c backlight -d "$device" set "$floor")
                        parse_brightness "$row"
                        icon=display-brightness-warning title='Swipe again to turn off'
                    fi
                else
                    row=$(brightnessctl -m -c backlight -d "$device" --min-value="$floor" set "${step}%-")
                    parse_brightness "$row"
                    title="Brightness: $value%"
                fi
            else
                # Query on every upward action: idle DPMS can turn off at any brightness.
                get_dpms
                if (( dpms_off )); then
                    set_dpms enable
                    sleep 0.15
                    woke=1
                fi
                row=$(brightnessctl -m -c backlight -d "$device" set "+${step}%")
                parse_brightness "$row"
                title="Brightness: $value%"
                if (( woke )); then title="Screen On: $value%"; fi
            fi
            publish display
            ;;
        --kbd-bright-up|--kbd-bright-down|--kbd-bright-show|--kbd-bright-toggle)
            lock_control kbd
            if ! keyboard_device; then
                if [[ $action == --kbd-bright-show ]]; then return 0; fi
                notify dialog-error 'No Kbd Backlight Found'
                return 1
            fi
            case $action in
                --kbd-bright-up) row=$(brightnessctl -m -c leds -d "$device" set "+${step}%") ;;
                --kbd-bright-down) row=$(brightnessctl -m -c leds -d "$device" --min-value=0 set "${step}%-") ;;
                --kbd-bright-toggle)
                    if (( current )); then value=0; else value=100; fi
                    row=$(brightnessctl -m -c leds -d "$device" set "$value%") ;;
            esac
            parse_brightness "$row"
            icon=keyboard-brightness title="Kbd Brightness: $value%"
            publish kbd
            ;;
        --play-pause|--next|--prev|--stop)
            lock_control media
            media "$action"
            ;;
        *)
            printf 'Usage: %s {--vol-up|--vol-down|--vol-mute|--mic-mute|--bright-up|--bright-down|--kbd-bright-up|--kbd-bright-down|--kbd-bright-toggle|--kbd-bright-show|--play-pause|--next|--prev|--stop} [step: 1–100]\n' "$0" >&2
            return 1
            ;;
    esac
}

main "$@"
