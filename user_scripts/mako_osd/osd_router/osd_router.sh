#!/usr/bin/env bash
# Hyprland/Wayland OSD router. Hardware updates serialize per control;
# one asynchronous notifier per control coalesces rapid updates.
set -euo pipefail
export LC_ALL=C
SYNC_ID=sys-osd
RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$UID}
MEDIA_IPC=${BASH_SOURCE[0]%/*}/media_ipc.py
declare -A ICON_CACHE=()

notify() {
    local icon=$1 title=$2 value=${3:-}
    local name=$icon candidate
    case $name in
        gpm-brightness-lcd|display-brightness-*) name=xfpm-brightness-lcd ;;
        keyboard-brightness) name=input-keyboard-brightness ;;
    esac
    if [[ -n ${ICON_CACHE[$name]+present} ]]; then
        icon=${ICON_CACHE[$name]}
    else
        # Direct files avoid an expensive theme lookup on every held-key repeat.
        # Papirus is shipped with the ISO; retain named icons on other installations.
        for candidate in /usr/share/icons/Papirus/{32x32,24x24}/{status,panel,actions,devices}/"$name.svg"; do
            if [[ -f $candidate ]]; then icon=$candidate; break; fi
        done
        ICON_CACHE[$name]=$icon
    fi
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
    exec {worker_fd}> "$RUNTIME_DIR/osd_${group}_state.lock"
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

publish_state() {
    local group=$1 state_lock_fd
    exec {state_lock_fd}> "$RUNTIME_DIR/osd_${group}_state.lock"
    flock -x "$state_lock_fd"
    printf '%s|%s|%s\n' "$icon" "$title" "$value" > "$state_file"
    exec {state_lock_fd}>&-
    ui_worker "$group" </dev/null >/dev/null &
}

publish() {
    local group=$1
    exec {lock_fd}>&-
    publish_state "$group"
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

# Discover the primary internal display backlight directly via sysfs (zero forks).
# Ranks kernel backlight types (raw > firmware > platform) + driver priority, breaking ties by max_brightness.
backlight_device() {
    local dir b_type name rank drv_rank best_rank=0 best_max=0 cur max
    for dir in /sys/class/backlight/*; do
        [[ -e $dir/brightness && -e $dir/max_brightness ]] || continue
        cur=$(< "$dir/brightness")
        max=$(< "$dir/max_brightness")
        [[ $cur =~ ^[0-9]+$ && $max =~ ^[1-9][0-9]*$ ]] || continue
        b_type=''
        [[ -r $dir/type ]] && b_type=$(< "$dir/type")
        case $b_type in
            raw)      rank=3000 ;;
            firmware) rank=2000 ;;
            *)        rank=1000 ;;
        esac
        name=${dir##*/}
        case ${name,,} in
            intel_backlight*) drv_rank=400 ;;
            amdgpu_bl*)       drv_rank=350 ;;
            nvidia*)          drv_rank=300 ;;
            apple*)           drv_rank=280 ;;
            ddcci*)           drv_rank=250 ;;
            *backlight*)      drv_rank=200 ;;
            acpi_video*)      drv_rank=100 ;;
            *)                drv_rank=150 ;;
        esac
        rank=$(( rank + drv_rank ))
        if (( rank > best_rank || (rank == best_rank && max > best_max) )); then
            best_rank=$rank
            best_max=$max
            device=$name
            current=$cur
            maximum=$max
        fi
    done
    (( best_rank > 0 )) || return 1
    value=$(( (current * 100 + maximum / 2) / maximum ))
}

write_backlight() {
    local target=$1 sys_path="/sys/class/backlight/$device/brightness"
    if [[ -w $sys_path ]] && printf '%s\n' "$target" > "$sys_path" 2>/dev/null; then
        current=$target
        value=$(( (current * 100 + maximum / 2) / maximum ))
        return 0
    fi
    if command -v brightnessctl >/dev/null 2>&1 &&
       brightnessctl -q -c backlight -d "$device" set "$target" >/dev/null 2>&1; then
        current=$target
        value=$(( (current * 100 + maximum / 2) / maximum ))
        return 0
    fi
    if command -v busctl >/dev/null 2>&1 &&
       busctl --system call org.freedesktop.login1 /org/freedesktop/login1/session/auto \
           org.freedesktop.login1.Session SetBrightness ssu backlight "$device" "$target" >/dev/null 2>&1; then
        current=$target
        value=$(( (current * 100 + maximum / 2) / maximum ))
        return 0
    fi
    return 1
}

keyboard_device() {
    local dir cur max
    for dir in /sys/class/leds/*kbd_backlight*; do
        [[ -e $dir/brightness && -e $dir/max_brightness ]] || continue
        cur=$(< "$dir/brightness")
        max=$(< "$dir/max_brightness")
        [[ $cur =~ ^[0-9]+$ && $max =~ ^[1-9][0-9]*$ ]] || continue
        device=${dir##*/}
        current=$cur
        maximum=$max
        value=$(( (current * 100 + maximum / 2) / maximum ))
        return 0
    done
    local devices
    devices=$(brightnessctl -m -l -c leds 2>/dev/null) || return 1
    while IFS= read -r row; do
        if [[ ${row%%,*} == *kbd_backlight* ]]; then
            parse_brightness "$row"
            return 0
        fi
    done <<< "$devices"
    return 1
}

write_kbd_backlight() {
    local target=$1 sys_path="/sys/class/leds/$device/brightness"
    if [[ -w $sys_path ]] && printf '%s\n' "$target" > "$sys_path" 2>/dev/null; then
        current=$target
        value=$(( (current * 100 + maximum / 2) / maximum ))
        return 0
    fi
    if command -v brightnessctl >/dev/null 2>&1 &&
       brightnessctl -q -c leds -d "$device" set "$target" >/dev/null 2>&1; then
        current=$target
        value=$(( (current * 100 + maximum / 2) / maximum ))
        return 0
    fi
    if command -v busctl >/dev/null 2>&1 &&
       busctl --system call org.freedesktop.login1 /org/freedesktop/login1/session/auto \
           org.freedesktop.login1.Session SetBrightness ssu leds "$device" "$target" >/dev/null 2>&1; then
        current=$target
        value=$(( (current * 100 + maximum / 2) / maximum ))
        return 0
    fi
    return 1
}

get_dpms() {
    local monitors='' sock="$RUNTIME_DIR/hypr/${HYPRLAND_INSTANCE_SIGNATURE:-}/.socket.sock"
    dpms_off=0
    if [[ -n ${HYPRLAND_INSTANCE_SIGNATURE:-} && -S $sock ]]; then
        monitors=$(socat - "UNIX-CONNECT:$sock" <<< /monitors 2>/dev/null) || monitors=''
    fi
    if [[ -z $monitors ]]; then
        monitors=$(hyprctl monitors 2>/dev/null) || return 0
    fi
    # Active outputs only: disabled connectors must not trigger wake/off handling.
    if [[ $monitors =~ dpmsStatus:[[:space:]]*(0|false) ]]; then dpms_off=1; fi
}

set_dpms() {
    local result
    result=$(hyprctl eval "hl.dispatch(hl.dsp.dpms({ action = \"$1\" }))" 2>/dev/null) || result=''
    if [[ $result != ok ]]; then
        local arg=on
        [[ $1 == disable ]] && arg=off
        result=$(hyprctl dispatch dpms "$arg" 2>/dev/null) || result=''
    fi
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
    # Focus only ranks players; it is not required to control them. A terminal
    # or launcher can retain an old instance signature after Hyprland restarts.
    if ! clients=$(hyprctl -j clients 2>/dev/null) ||
        ! jq -e 'type == "array"' >/dev/null 2>&1 <<< "$clients"; then
        clients='[]'
    fi
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
            break
        fi
        sleep 0.025
    done
    media_remember
    case $status in
        Playing) icon=media-playback-start ;;
        Paused) icon=media-playback-pause ;;
        *) icon=media-playback-stop ;;
    esac
    if [[ -n $metadata ]]; then
        title="$status: $metadata"
    else
        title=$status
    fi
    publish media
}

main() {
    local action=${1:-} step=${2:-5} gesture_id=${3:-}
    if (( $# > 3 )) || [[ ! $step =~ ^[0-9]{1,3}$ ]] || (( 10#$step < 1 || 10#$step > 100 )) || [[ ! $gesture_id =~ ^[A-Za-z0-9_.:-]*$ ]]; then
        printf 'Step must be an integer from 1 to 100; at most three arguments are accepted.\n' >&2
        return 1
    fi
    step=$((10#$step))
    local icon title value='' lock_fd state_file device current maximum row volume
    case $action in
        --vol-up|--vol-down)
            local group=audio
            local delta_file=$RUNTIME_DIR/osd_audio_delta.txt
            local cache_file=$RUNTIME_DIR/osd_audio_cache.txt
            local q_lock_fd pending=0 signed_step=$step
            [[ $action == --vol-down ]] && signed_step=$(( -step ))
            state_file=$RUNTIME_DIR/osd_audio_state.txt

            # Deposit signed step into the shared accumulator and return immediately if a worker is already active.
            exec {q_lock_fd}> "$RUNTIME_DIR/osd_audio_q.lock"
            flock -x "$q_lock_fd"
            if [[ -f $delta_file ]]; then
                pending=$(< "$delta_file")
                [[ $pending =~ ^-?[0-9]+$ ]] || pending=0
            fi
            pending=$(( pending + signed_step ))
            printf '%s\n' "$pending" > "$delta_file"
            flock -u "$q_lock_fd"

            exec {lock_fd}> "$RUNTIME_DIR/osd_audio.lock"
            if ! flock -n "$lock_fd"; then
                exec {q_lock_fd}>&- {lock_fd}>&-
                return 0
            fi

            local backend=''
            if command -v wpctl >/dev/null 2>&1; then
                backend=wpctl
            elif command -v pamixer >/dev/null 2>&1; then
                backend=pamixer
            else
                exec {q_lock_fd}>&- {lock_fd}>&-
                printf 'Neither wpctl nor pamixer found.\n' >&2
                return 1
            fi

            local net_delta=0 now_us cache_ts=0 cur_val=-1 cur_muted=0 target new_muted wp_out
            while true; do
                flock -x "$q_lock_fd"
                net_delta=0
                if [[ -f $delta_file ]]; then
                    net_delta=$(< "$delta_file")
                    [[ $net_delta =~ ^-?[0-9]+$ ]] || net_delta=0
                fi
                if (( net_delta == 0 )); then
                    # Release the hardware lock while still holding the queue lock so any
                    # incoming writer is guaranteed to acquire the worker lock after depositing.
                    exec {lock_fd}>&-
                    flock -u "$q_lock_fd"
                    exec {q_lock_fd}>&-
                    break
                fi
                printf '0\n' > "$delta_file"
                flock -u "$q_lock_fd"

                now_us=${EPOCHREALTIME/./}
                if (( cur_val < 0 )) && [[ -f $cache_file ]]; then
                    IFS=' ' read -r cache_ts cur_val cur_muted < "$cache_file" || true
                    if [[ ! $cache_ts =~ ^[0-9]+$ || ! $cur_val =~ ^[0-9]+$ || ! $cur_muted =~ ^[01]$ ]] || (( now_us - cache_ts > 2000000 )); then
                        cur_val=-1
                        cur_muted=0
                    fi
                fi

                if [[ $backend == wpctl ]]; then
                    if (( cur_val < 0 )); then
                        wp_out=$(wpctl get-volume @DEFAULT_AUDIO_SINK@)
                        cur_muted=0
                        [[ $wp_out == *'[MUTED]'* ]] && cur_muted=1
                        if [[ $wp_out =~ ([0-9]+)\.([0-9]{2}) ]]; then
                            cur_val=$(( 10#${BASH_REMATCH[1]} * 100 + 10#${BASH_REMATCH[2]} ))
                        else
                            printf 'Invalid wpctl output: %s\n' "$wp_out" >&2
                            return 1
                        fi
                    fi
                    target=$(( cur_val + net_delta ))
                    (( target > 100 )) && target=100
                    (( target < 0 )) && target=0
                    new_muted=$cur_muted
                    (( net_delta > 0 )) && new_muted=0
                    if (( new_muted != cur_muted )); then
                        wpctl set-mute @DEFAULT_AUDIO_SINK@ "$new_muted"
                    fi
                    if (( target != cur_val )); then
                        wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ "${target}%"
                    fi
                    cur_val=$target
                    cur_muted=$new_muted
                    now_us=${EPOCHREALTIME/./}
                    printf '%s %s %s\n' "$now_us" "$cur_val" "$cur_muted" > "$cache_file"
                else
                    if (( cur_val < 0 )); then
                        local -a mixer=(pamixer)
                        if (( net_delta > 0 )); then
                            mixer+=(-u --increase "$net_delta")
                        else
                            mixer+=(--decrease "$(( -net_delta ))")
                        fi
                        volume=$("${mixer[@]}" --get-volume-human)
                        if [[ $volume == muted ]]; then
                            cur_muted=1
                            cur_val=-1
                            rm -f "$cache_file"
                        elif [[ $volume =~ ^([0-9]+)%$ ]]; then
                            cur_muted=0
                            cur_val=$(( 10#${BASH_REMATCH[1]} ))
                            now_us=${EPOCHREALTIME/./}
                            printf '%s %s 0\n' "$now_us" "$cur_val" > "$cache_file"
                        else
                            printf 'Invalid pamixer output: %s\n' "$volume" >&2
                            return 1
                        fi
                    else
                        target=$(( cur_val + net_delta ))
                        (( target > 100 )) && target=100
                        (( target < 0 )) && target=0
                        new_muted=$cur_muted
                        (( net_delta > 0 )) && new_muted=0
                        if (( new_muted != cur_muted && target != cur_val )); then
                            pamixer -u --set-volume "$target"
                        elif (( new_muted != cur_muted )); then
                            pamixer -u
                        elif (( target != cur_val )); then
                            pamixer --set-volume "$target"
                        fi
                        cur_val=$target
                        cur_muted=$new_muted
                        now_us=${EPOCHREALTIME/./}
                        printf '%s %s %s\n' "$now_us" "$cur_val" "$cur_muted" > "$cache_file"
                    fi
                fi

                if (( cur_muted )); then
                    icon=audio-volume-muted title='Audio Muted' value=''
                else
                    value=$cur_val
                    icon=audio-volume-high title="Volume: $value%"
                fi
                publish_state audio
            done
            ;;
        --vol-mute|--mic-mute)
            local group=audio
            if [[ $action == --mic-mute ]]; then
                group=mic
            else
                local q_lock_fd
                exec {q_lock_fd}> "$RUNTIME_DIR/osd_audio_q.lock"
                flock -x "$q_lock_fd"
                rm -f "$RUNTIME_DIR/osd_audio_delta.txt" "$RUNTIME_DIR/osd_audio_cache.txt"
                exec {q_lock_fd}>&-
            fi
            lock_control "$group"
            if command -v wpctl >/dev/null 2>&1; then
                local target='@DEFAULT_AUDIO_SINK@' wp_out raw_val=0
                [[ $group == mic ]] && target='@DEFAULT_AUDIO_SOURCE@'
                wpctl set-mute "$target" toggle
                wp_out=$(wpctl get-volume "$target")
                if [[ $wp_out =~ ([0-9]+)\.([0-9]{2}) ]]; then
                    raw_val=$(( 10#${BASH_REMATCH[1]} * 100 + 10#${BASH_REMATCH[2]} ))
                else
                    printf 'Invalid wpctl output: %s\n' "$wp_out" >&2
                    return 1
                fi
                if [[ $wp_out == *'[MUTED]'* ]]; then
                    volume=muted
                    value=0
                    [[ $group == audio ]] && printf '%s %s 1\n' "${EPOCHREALTIME/./}" "$raw_val" > "$RUNTIME_DIR/osd_audio_cache.txt"
                else
                    value=$raw_val
                    volume="${value}%"
                    [[ $group == audio ]] && printf '%s %s 0\n' "${EPOCHREALTIME/./}" "$raw_val" > "$RUNTIME_DIR/osd_audio_cache.txt"
                fi
            elif command -v pamixer >/dev/null 2>&1; then
                local -a mixer=(pamixer)
                [[ $group == mic ]] && mixer+=(--default-source)
                volume=$("${mixer[@]}" --toggle-mute --get-volume-human)
                if [[ $volume == muted ]]; then
                    value=0
                elif [[ $volume =~ ^([0-9]+)%$ ]]; then
                    value=$((10#${BASH_REMATCH[1]}))
                    [[ $group == audio ]] && printf '%s %s 0\n' "${EPOCHREALTIME/./}" "$value" > "$RUNTIME_DIR/osd_audio_cache.txt"
                else
                    printf 'Invalid pamixer output: %s\n' "$volume" >&2
                    return 1
                fi
            else
                printf 'Neither wpctl nor pamixer found.\n' >&2
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
            # Exit cleanly on desktop systems or external-only monitors with no internal backlight
            if ! backlight_device; then
                return 0
            fi
            local last_title='' _ignored floor woke=0 dpms_off=0 delta target can_turn_off=0 wake_locked=0
            local floor_ts_file=$RUNTIME_DIR/osd_display_floor_ts.txt
            local off_ts_file=$RUNTIME_DIR/osd_display_off_ts.txt
            local active_ts_file=$RUNTIME_DIR/osd_display_active_ts.txt
            local wake_ts_file=$RUNTIME_DIR/osd_display_wake_ts.txt
            local now_us=${EPOCHREALTIME/./} last_floor_us=0 last_off_us=0 last_active_us=0 last_wake_us=0
            local floor_gid='' off_gid='' active_gid='' wake_gid=''
            if [[ -f $state_file ]]; then
                IFS='|' read -r _ignored last_title _ignored < "$state_file" || true
            fi
            if [[ -f $floor_ts_file ]]; then
                IFS=' ' read -r last_floor_us floor_gid < "$floor_ts_file" || true
                [[ $last_floor_us =~ ^[0-9]+$ ]] || { last_floor_us=0; floor_gid=''; }
            fi
            if [[ -f $off_ts_file ]]; then
                IFS=' ' read -r last_off_us off_gid < "$off_ts_file" || true
                [[ $last_off_us =~ ^[0-9]+$ ]] || { last_off_us=0; off_gid=''; }
            fi
            if [[ -f $wake_ts_file ]]; then
                IFS=' ' read -r last_wake_us wake_gid < "$wake_ts_file" || true
                [[ $last_wake_us =~ ^[0-9]+$ ]] || { last_wake_us=0; wake_gid=''; }
            fi
            floor=$(( (maximum + 99) / 100 ))
            (( floor < 1 )) && floor=1
            delta=$(( (step * maximum + 50) / 100 ))
            (( delta < 1 )) && delta=1
            icon=gpm-brightness-lcd
            if [[ $action == --bright-down ]]; then
                rm -f "$wake_ts_file"
                if (( current <= floor )); then
                    if [[ -n "$gesture_id" && -n "$off_gid" && $gesture_id == "$off_gid" ]]; then
                        dpms_off=1
                    else
                        get_dpms
                    fi
                    if (( dpms_off )); then
                        icon=display-brightness-off title='Screen Off' value=0
                    else
                        if [[ $last_title == 'Swipe again to turn off' ]] && (( last_floor_us > 0 )); then
                            if [[ -n "$gesture_id" ]]; then
                                if [[ $gesture_id != "$floor_gid" ]] && (( now_us - last_floor_us > 150000 )); then
                                    can_turn_off=1
                                fi
                            elif (( now_us - last_floor_us > 400000 )); then
                                can_turn_off=1
                            fi
                        fi
                        if (( can_turn_off )); then
                            set_dpms disable
                            now_us=${EPOCHREALTIME/./}
                            printf '%s %s\n' "$now_us" "$gesture_id" > "$off_ts_file"
                            rm -f "$active_ts_file" "$floor_ts_file"
                            icon=display-brightness-off title='Screen Off' value=0
                        else
                            write_backlight "$floor"
                            if [[ $last_title != 'Swipe again to turn off' || -z "$gesture_id" || $gesture_id == "$floor_gid" ]]; then
                                printf '%s %s\n' "$now_us" "$gesture_id" > "$floor_ts_file"
                            fi
                            icon=display-brightness-warning title='Swipe again to turn off'
                        fi
                    fi
                else
                    target=$(( current - delta ))
                    (( target < floor )) && target=$floor
                    write_backlight "$target"
                    rm -f "$off_ts_file"
                    if (( current <= floor )); then
                        printf '%s %s\n' "$now_us" "$gesture_id" > "$floor_ts_file"
                        icon=display-brightness-warning title='Swipe again to turn off'
                    else
                        rm -f "$floor_ts_file"
                        title="Brightness: $value%"
                    fi
                fi
            else
                # Ignore lift-off rebound for 600ms immediately after turning screen off
                if (( last_off_us > 0 && now_us - last_off_us < 600000 )); then
                    return 0
                fi
                if [[ -f $active_ts_file ]]; then
                    IFS=' ' read -r last_active_us active_gid < "$active_ts_file" || true
                    [[ $last_active_us =~ ^[0-9]+$ ]] || { last_active_us=0; active_gid=''; }
                fi
                # Lock remaining ticks of the wake gesture (or 400ms non-gesture wake window) to floor
                if (( last_wake_us > 0 )); then
                    if [[ -n "$gesture_id" ]]; then
                        if [[ $gesture_id == "$wake_gid" ]] || (( now_us - last_wake_us < 150000 )); then
                            wake_locked=1
                        fi
                    elif (( now_us - last_wake_us < 400000 )); then
                        wake_locked=1
                    fi
                fi
                if (( wake_locked )); then
                    if (( current != floor )); then
                        write_backlight "$floor"
                    fi
                    if [[ -z "$gesture_id" || -z "$wake_gid" || $gesture_id == "$wake_gid" ]]; then
                        printf '%s %s\n' "$now_us" "${gesture_id:-$wake_gid}" > "$wake_ts_file"
                    fi
                    printf '%s %s\n' "$now_us" "$gesture_id" > "$active_ts_file"
                    title="Screen On: $value%"
                else
                    rm -f "$floor_ts_file" "$off_ts_file" "$wake_ts_file"
                    # Query DPMS on new gesture, outside the 2-second active window, or if previously off
                    if [[ -z "$gesture_id" || $gesture_id != "$active_gid" ]] || (( now_us - last_active_us > 2000000 )) || [[ $last_title == 'Screen Off' ]]; then
                        get_dpms
                        if (( dpms_off )); then
                            write_backlight "$floor"
                            set_dpms enable
                            sleep 0.15
                            write_backlight "$floor"
                            woke=1
                        fi
                    fi
                    now_us=${EPOCHREALTIME/./}
                    printf '%s %s\n' "$now_us" "$gesture_id" > "$active_ts_file"
                    if (( woke )); then
                        printf '%s %s\n' "$now_us" "$gesture_id" > "$wake_ts_file"
                        title="Screen On: $value%"
                    else
                        target=$(( current + delta ))
                        (( target > maximum )) && target=$maximum
                        write_backlight "$target"
                        title="Brightness: $value%"
                    fi
                fi
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
            local delta=$(( (step * maximum + 50) / 100 )) target
            (( delta < 1 )) && delta=1
            case $action in
                --kbd-bright-up)
                    target=$(( current + delta ))
                    (( target > maximum )) && target=$maximum
                    write_kbd_backlight "$target"
                    ;;
                --kbd-bright-down)
                    target=$(( current - delta ))
                    (( target < 0 )) && target=0
                    write_kbd_backlight "$target"
                    ;;
                --kbd-bright-toggle)
                    if (( current )); then target=0; else target=$maximum; fi
                    write_kbd_backlight "$target"
                    ;;
            esac
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
