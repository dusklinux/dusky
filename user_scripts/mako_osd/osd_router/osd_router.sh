#!/usr/bin/env bash
# Hyprland/Wayland OSD router. Hardware updates serialize per control;
# one asynchronous notifier per control coalesces rapid updates.
set -euo pipefail
export LC_ALL=C
SYNC_ID=sys-osd
RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$UID}
MEDIA_IPC=$(dirname -- "${BASH_SOURCE[0]}")/media_ipc.py

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
        # Titles may contain pipes. Only the first and last delimiters frame
        # fields; display text has already had newlines removed by media_read().
        worker_icon=${snapshot%%|*}
        worker_value=${snapshot##*|}
        worker_title=${snapshot#*|}
        worker_title=${worker_title%|*}
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

# Read typed MPRIS properties together. Metadata is optional and cannot corrupt
# the record framing: control characters are replaced only in display text.
# Outputs use media()'s local variables, just as parse_brightness() uses main()'s.
media_read() {
    local data record
    if [[ -n ${2:-} ]]; then
        data=$2
    elif [[ $1 == mpv:* ]]; then
        data=$(python3 "$MEDIA_IPC" read "$1") || return
    else
        data=$("${media_bus[@]}" call "$1" /org/mpris/MediaPlayer2 \
            org.freedesktop.DBus.Properties GetAll s org.mpris.MediaPlayer2.Player) || return
    fi
    record=$(jq -er '
        .data[0] as $p | ($p.Metadata.data // {}) as $m
        | select($p.PlaybackStatus.data | IN("Playing", "Paused", "Stopped"))
        | [($p.PlaybackStatus.data), ($m["mpris:trackid"].data // ""),
           ([($m["xesam:artist"].data // [] | join(", ")),
             ($m["xesam:title"].data // "")] | map(select(. != "")) | join(" - ")
             | gsub("[\u0000-\u001f\u007f]"; " ")),
           $p.CanControl.data, $p.CanPlay.data, $p.CanPause.data,
           $p.CanGoNext.data, $p.CanGoPrevious.data]
        | map(if . == null then "false" else tostring end) | join("\u001f")
    ' <<< "$data") || return
    IFS=$'\x1f' read -r status track metadata can_control can_play can_pause can_next can_prev <<< "$record"
}

media_error() {
    printf 'Media: %s\n' "$1" >&2
    return 1
}

media_remember() {
    printf '%s\n' "$destination" > "$player_file"
    # Remember other active players too: a newly started player should take
    # priority, while an already-playing background player must not steal resume.
    jq -n --arg selected "$destination" --arg status "$status" --args '
        {selected: $selected, playing: ($ARGS.positional | unique
          | map(select(. != $selected))
          | if $status == "Playing" then . + [$selected] else . end)}
    ' "${playing[@]}" > "$context_file"
}

media() {
    local action=$1 listing clients ordered name owner remembered='' ipc previous='' snapshot=''
    local status track metadata can_control can_play can_pause can_next can_prev
    local destination='' player='' best=99 rank command expected='' old_track old_metadata deadline now
    local player_file=$RUNTIME_DIR/osd_media_player.txt
    local context_file=$RUNTIME_DIR/osd_media_context.json
    local -a playing=()
    local -A was_playing=()
    # Systemd is part of the ISO. Direct MPRIS calls provide capabilities, typed
    # metadata, exact instance addressing and a bounded timeout without a daemon.
    local -a media_bus=(busctl --user --json=short --timeout=1 --auto-start=no)
    listing=$("${media_bus[@]}" --acquired list) || { media_error 'Cannot List Media Players'; return 1; }
    ipc=$(python3 "$MEDIA_IPC" list) || return
    clients=$(hyprctl -j clients) || { media_error 'Cannot Read Hyprland Windows'; return 1; }
    if [[ -f $player_file ]]; then remembered=$(< "$player_file"); fi
    if [[ -f $context_file ]]; then
        previous=$(jq -er --arg selected "$remembered" '
            select(.selected == $selected) | .playing[]' "$context_file" 2>/dev/null) || previous=''
    fi
    while IFS= read -r owner; do
        [[ -z $owner ]] || was_playing[$owner]=1
    done <<< "$previous"
    ordered=$(jq -nr --argjson bus "$listing" --argjson ipc "$ipc" --argjson clients "$clients" --arg remembered "$remembered" '
        [([$bus[] | select(.name | startswith("org.mpris.MediaPlayer2."))
             | . as $p | select(any($ipc[]; .pid == $p.pid) | not)] + $ipc)[]
         | . as $p
         | ([([$p.pid] + ($p.ancestors // []))[] as $pid
              | [$clients[] | select(.pid == $pid and .mapped and (.hidden | not))]
              | select(length > 0)][0] // []
              | map(.focusHistoryID | select(. >= 0)) | min // 2147483647) as $focus
         | {name, connection, focus: $focus, properties}]
        | sort_by((if .focus == 0 then 0 elif .connection == $remembered then 1 else 2 end),
                  .focus, .name)[]
        | [.name, .connection, .focus,
           (if .properties then (.properties | tojson) else "" end)] | join("\u001f")
    ') || return
    # Focused playing > newly playing > remembered > already playing > paused
    # > stopped. New playback supersedes a stale paused selection; other existing
    # playback does not split a deliberate pause/resume pair.
    # Focusing a paused browser must not steal playback keys.
    # PID matching distinguishes multiple instances; focus history breaks ties.
    # Cache the unique bus owner, so restarting an app cannot inherit stale choice.
    local history
    while IFS=$'\x1f' read -r name owner history snapshot; do
        [[ -n $name && ( $owner == :* || $owner == mpv:* ) ]] || continue
        media_read "$owner" "$snapshot" 2>/dev/null || continue # A player may exit during discovery.
        [[ $can_control == true ]] || continue
        if [[ $status == Playing ]]; then playing+=("$owner"); fi
        if [[ $status == Playing && $history == 0 ]]; then rank=0
        elif [[ $status == Playing && ! ${was_playing[$owner]+present} ]]; then rank=1
        elif [[ $owner == "$remembered" ]]; then rank=2
        elif [[ $status == Playing ]]; then rank=3
        elif [[ $status == Paused ]]; then rank=4
        else rank=5
        fi
        if (( rank < best )); then
            best=$rank destination=$owner player=${name#org.mpris.MediaPlayer2.}
        fi
    done <<< "$ordered"
    [[ -n $destination ]] || { media_error 'No Controllable Player'; return 1; }
    # Refresh after selection; never redirect an action if this instance vanishes.
    media_read "$destination" || { media_error "Player Unavailable: $player"; return 1; }
    [[ $can_control == true ]] || { media_error "Controls Unavailable: $player"; return 1; }
    case $action in
        --play-pause)
            if [[ $status == Playing ]]; then command=Pause expected=Paused
            else command=Play expected=Playing
            fi ;;
        --play) command=Play expected=Playing ;;
        --pause) command=Pause expected=Paused ;;
        --stop) command=Stop expected=Stopped ;;
        --next) command=Next ;;
        --prev) command=Previous ;;
    esac
    case $command in
        Play) [[ $can_play == true || $status == Playing ]] || { media_error "Cannot Play: $player"; return 1; } ;;
        Pause) [[ $can_pause == true || $status == Paused ]] || { media_error "Cannot Pause: $player"; return 1; } ;;
        Next) [[ $can_next == true ]] || { media_error "No Next Track: $player"; return 1; } ;;
        Previous) [[ $can_prev == true ]] || { media_error "No Previous Track: $player"; return 1; } ;;
    esac
    old_track=$track old_metadata=$metadata
    snapshot=''
    if [[ $destination == mpv:* ]]; then
        snapshot=$(python3 "$MEDIA_IPC" control "$destination" "$command") || { media_error "Media Command Failed: $player"; return 1; }
    else
        "${media_bus[@]}" call "$destination" /org/mpris/MediaPlayer2 org.mpris.MediaPlayer2.Player "$command" \
            >/dev/null || { media_error "Media Command Failed: $player"; return 1; }
    fi
    now=${EPOCHREALTIME/./}
    deadline=$((now + 1000000))
    while true; do
        media_read "$destination" "$snapshot" || { media_error "Player Unavailable: $player"; return 1; }
        snapshot=''
        if [[ -n $expected ]]; then
            [[ $status != "$expected" ]] || break
        elif [[ $track != "$old_track" || $metadata != "$old_metadata" || $status == Stopped ]]; then
            break
        fi
        now=${EPOCHREALTIME/./}
        if (( now >= deadline )); then
            if [[ -n $expected ]]; then
                media_error "Player Did Not $command: $player"
                return 1
            fi
            # Previous may restart the same track; repeat can keep the same ID.
            # A successful method reply alone cannot confirm a track change.
            media_remember
            exec {lock_fd}>&-
            return
        fi
        sleep 0.025
    done
    media_remember
    exec {lock_fd}>&-
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
        --play-pause|--play|--pause|--next|--prev|--stop)
            lock_control media
            media "$action"
            ;;
        *)
            printf 'Usage: %s {--vol-up|--vol-down|--vol-mute|--mic-mute|--bright-up|--bright-down|--kbd-bright-up|--kbd-bright-down|--kbd-bright-toggle|--kbd-bright-show|--play-pause|--play|--pause|--next|--prev|--stop} [step: 1–100]\n' "$0" >&2
            return 1
            ;;
    esac
}

main "$@"
