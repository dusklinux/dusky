#!/usr/bin/env bash
# Bash 5.3+; Wayland Rofi, pactl, jq, notify-send.
# Optional: busctl for MPRIS, or executable CLI controllers.
# List media sessions and additional streams with measured audio activity.
set -euo pipefail

readonly APP_NAME=mpris_playback NOTIFY_ICON=multimedia-audio-player-symbolic
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
readonly SCRIPT_DIR CONTROLLERS_DIR="$SCRIPT_DIR/controllers"
readonly ROFI_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/rofi/config.rasi"
readonly ROFI_THEME='window {width: 40%;} listview {lines: 10;}'
# Metadata protocol: title, artist, position, duration, status separated by US.
# US is not IFS whitespace, so empty fields survive read. Display data is one line.
readonly SEP=$'\x1f'
readonly ICON_PLAYING='󰐊' ICON_PAUSED='󰏤' ICON_STOPPED='󰓛' ICON_UNKNOWN='󰝚'
readonly ICON_TOGGLE='󰐎' ICON_NEXT='󰒭' ICON_PREV='󰒮' ICON_BACK='󰁍' ICON_GROUP='󰉋'

HAS_MPRIS=0
declare -a ids=() apps=() media=() corked=() muted=() pids=() binaries=() backends=() entries=() audible=()
declare -a players=()
declare -A player_pids=() metadata_cache=() used_backends=() pid_counts=() app_counts=()
# Shared output variables avoid spawning subshells for string formatting.
REPLY='' title='' artist='' position='' duration='' status=''

_notify() {
    local body=${2:-}
    # Notification bodies support markup; metadata should display literally.
    body=${body//&/\&amp;}; body=${body//</\&lt;}; body=${body//>/\&gt;}
    notify-send -a "$APP_NAME" -i "$NOTIFY_ICON" \
        -h string:x-canonical-private-synchronous:mpris_ctl -- "$1" "$body" || true
}

_single_line() {
    REPLY=${1//[$'\r\n\x1f']/ }
}

_clean_media_name() {
    REPLY=$1
    if [[ $REPLY == *'://'* && $REPLY == *'?'* ]]; then
        if [[ $REPLY == *' - '* ]]; then
            REPLY=${REPLY##* - }
            case $REPLY in mpv|vlc|firefox) REPLY='' ;; esac
        else
            REPLY=''
        fi
    fi
    REPLY=${REPLY% - YouTube}
    REPLY=${REPLY% - Twitch}
    REPLY=${REPLY% - mpv}
    REPLY=${REPLY% - VLC media player}
    REPLY=${REPLY% - Firefox}
}

_status_icon() {
    case ${1,,} in
        playing) REPLY=$ICON_PLAYING ;;
        paused|corked|muted) REPLY=$ICON_PAUSED ;;
        stopped|suspended) REPLY=$ICON_STOPPED ;;
        *) REPLY=$ICON_UNKNOWN ;;
    esac
}

_truncate() {
    REPLY=$1
    if ((${#REPLY} > $2)); then REPLY=${REPLY:0:$2-1}…; fi
}

_rofi_menu() {
    local prompt=$1 output rc=0
    shift
    local -a args=(-dmenu -i -no-custom -no-multi-select -no-markup-rows
        -format i -p "$prompt" -theme-str "$ROFI_THEME")
    [[ ! -f $ROFI_CONFIG ]] || args+=(-config "$ROFI_CONFIG")
    [[ -z ${ROFI_MESSAGE:-} ]] || args+=(-mesg "$ROFI_MESSAGE")
    output=$(printf '%s\n' "$@" | rofi "${args[@]}") || rc=$?
    case $rc in
        0)
            if [[ $output =~ ^[0-9]+$ ]] && ((10#$output < $#)); then
                REPLY=$((10#$output))
                return 0
            fi
            ;;
        1|1[0-9]|2[0-8]) return 1 ;; # Cancelled or an unused custom keybinding.
    esac
    _notify 'Execution Failed' 'Rofi did not return a valid selection.'
    return 2
}

_discover_sources() {
    local sinks clients activity
    sinks=$(pactl --format=json list sink-inputs) || return 1
    # Resolve client PIDs only when a stream omits its own PID.
    clients='[]'
    if jq -e 'any(.[]; (.properties["application.process.id"] // "") == "")' \
        >/dev/null <<< "$sinks"; then
        clients=$(pactl --format=json list clients) || return 1
    fi
    activity=$(python3 "$SCRIPT_DIR/audio_activity.py" <<< "$sinks") || return 1
    jq -r --argjson clients "$clients" --argjson activity "$activity" '
        def text: tostring | gsub("[\u0000-\u001f]"; " ");
        ($clients | map({key: (.index | tostring),
            value: (.properties["application.process.id"] // "")}) | from_entries) as $pids
        | sort_by(.corked, .index)[]
        | .properties as $p
        | [.index, ($p["application.name"] // ""), ($p["media.name"] // ""),
           (if .corked then "yes" else "no" end), (if .mute then "yes" else "no" end),
           (($p["application.process.id"] // "") as $pid
             | if $pid != "" then $pid else ($pids[.client | tostring] // "") end),
           ($p["application.process.binary"] // ""), ($activity[.index | tostring] // "unknown")]
        | map(text) | join("\u001f")' <<< "$sinks"
}

_update_players() {
    players=(); player_pids=()
    ((HAS_MPRIS)) || return 0
    local listing name pid rest
    listing=$(busctl --user --no-pager --no-legend --full --acquired list) || return 0
    while read -r name pid rest; do
        [[ $name == org.mpris.MediaPlayer2.* ]] || continue
        name=${name#org.mpris.MediaPlayer2.}
        players+=("$name")
        player_pids[$name]=$pid
    done <<< "$listing"
}

_backend_metadata() {
    local backend=$1 data=''
    if [[ ${metadata_cache[$backend]+present} ]]; then
        data=${metadata_cache[$backend]}
    else
        case $backend in
            mpris:*)
                # GetAll provides typed metadata without delimiter collisions.
                # Address the full bus name so multiple player instances stay distinct.
                data=$(busctl --user --json=short --timeout=2 --auto-start=no call \
                    "org.mpris.MediaPlayer2.${backend#mpris:}" /org/mpris/MediaPlayer2 \
                    org.freedesktop.DBus.Properties GetAll s org.mpris.MediaPlayer2.Player \
                    2>/dev/null) || data=''
                if [[ -n $data ]]; then
                    data=$(jq -er '
                        def text: tostring | gsub("[\u0000-\u001f\u007f]"; " ");
                        def pad: tostring | if length < 2 then "0" + . else . end;
                        def time:
                            if . == null then "" else
                                (. / 1000000 | floor | if . < 0 then 0 else . end) as $s
                                | ($s % 60 | pad) as $sec
                                | if $s < 3600 then "\($s / 60 | floor):\($sec)"
                                  else "\($s / 3600 | floor):\($s / 60 | floor | . % 60 | pad):\($sec)"
                                  end
                            end;
                        .data[0] as $p | ($p.Metadata.data // {}) as $m
                        | [($m["xesam:title"].data // ""),
                           ($m["xesam:artist"].data // [] | join(", ")),
                           ($p.Position.data | time), ($m["mpris:length"].data | time),
                           ($p.PlaybackStatus.data // "")]
                        | map(text) | join("\u001f")' <<< "$data" 2>/dev/null) || data=''
                fi
                ;;
            # An optional remote MPD connection must not stall source discovery.
            cli:*) data=$(timeout --kill-after=1 2 "$CONTROLLERS_DIR/${backend#cli:}" now 2>/dev/null) || data='' ;;
        esac
        data=${data//[$'\r\n']/ }
        metadata_cache[$backend]=$data
    fi
    title=''; artist=''; position=''; duration=''; status=''
    IFS=$SEP read -r title artist position duration status <<< "$data"
}

_matches_stream() {
    local i=$1 stream_title
    if [[ -z ${pids[i]} ]] || ((${pid_counts[${pids[i]}]:-0} < 2)); then return 0; fi
    _clean_media_name "${media[i]}"; stream_title=$REPLY
    _clean_media_name "$title"
    [[ -n $stream_title && $stream_title == "$REPLY" ]]
}

_detect_backend() {
    local i=$1 name player base candidate player_pid
    REPLY=pactl
    for name in "${binaries[i]}" "${apps[i]}"; do
        name=${name,,}
        [[ -n $name && $name != */* && -x $CONTROLLERS_DIR/$name ]] || continue
        candidate=cli:$name
        [[ ! ${used_backends[$candidate]+present} ]] || continue
        _backend_metadata "$candidate"
        [[ -n $status ]] || continue # An installed controller needs a working client.
        _matches_stream "$i" || continue
        used_backends[$candidate]=1
        REPLY=$candidate
        return 0
    done
    for player in "${players[@]}"; do
        candidate=mpris:$player
        [[ ! ${used_backends[$candidate]+present} ]] || continue
        player_pid=${player_pids[$player]}
        base=${player%%.*}; base=${base,,}
        if [[ -n ${pids[i]} && $player_pid == "${pids[i]}" ]]; then
            :
        elif [[ $player_pid =~ ^[0-9]+$ && -n ${pids[i]} ]]; then
            continue
        elif [[ $base != "${binaries[i],,}" && $base != "${apps[i],,}" ]]; then
            continue
        fi
        _backend_metadata "$candidate"
        [[ -n $status ]] || continue
        # A global player may represent only one of several browser streams.
        # Do not attach it to an arbitrary tab solely because their PIDs match.
        _matches_stream "$i" || continue
        used_backends[$candidate]=1
        REPLY=$candidate
        return 0
    done
    REPLY=pactl
}

_read_metadata() {
    local i=$1
    _backend_metadata "${backends[i]}"
    _clean_media_name "${media[i]}"
    if [[ ${backends[i]} == pactl ]]; then
        case $REPLY in ''|'(null)'|'Audio Stream'|AudioStream|webm) title='Audio Stream' ;; esac
    fi
    if [[ ${2:-stream} == stream || -z $title ]]; then
        case $REPLY in ''|'(null)'|'Audio Stream'|AudioStream|webm) ;; *) title=$REPLY ;; esac
    fi
    # /proc cmdline is NUL-separated; preserve spaces and wildcard characters.
    if [[ -z $title && ${pids[i]} =~ ^[0-9]+$ ]]; then
        local -a argv=()
        local arg
        if mapfile -d '' -t argv 2>/dev/null < "/proc/${pids[i]}/cmdline"; then
            for arg in "${argv[@]:1}"; do
                [[ $arg != -* && ( $arg == */* || $arg == *.* ) ]] || continue
                _single_line "${arg##*/}"; title=$REPLY
                break
            done
        fi
    fi
    title=${title:-${apps[i]}}
    if [[ -z $status ]]; then
        local process_stat=''
        if [[ -n ${pids[i]} ]]; then
            IFS= read -r process_stat 2>/dev/null < "/proc/${pids[i]}/stat" || true
        fi
        # comm may itself contain spaces or parentheses; state follows its closing ).
        process_stat=${process_stat##*) }
        if [[ $process_stat == T\ * || $process_stat == t\ * ]]; then status=Suspended
        elif [[ ${corked[i]} == yes ]]; then status=Paused
        elif [[ ${muted[i]} == yes ]]; then status=Muted
        else status=Playing
        fi
    fi
}

_build_entry() {
    local i=$1 icon track time=''
    _read_metadata "$i"
    _status_icon "$status"; icon=$REPLY
    _truncate "$title" 45; track=$REPLY
    if [[ -n $artist ]]; then
        _truncate "$artist" 25; track="$REPLY · $track"
    fi
    if [[ -n $position ]]; then
        time="  [$position${duration:+/$duration}]"
    fi
    REPLY="$icon  ${apps[i]}  $track$time"
}

_load_sources() {
    local raw id app track cork mute pid binary activity i
    if ! raw=$(_discover_sources); then
        _notify 'Discovery Failed' 'Could not query the audio server.'
        return 1
    fi
    ids=(); apps=(); media=(); corked=(); muted=(); pids=(); binaries=(); backends=(); entries=(); audible=()
    metadata_cache=(); used_backends=(); pid_counts=(); app_counts=()
    while IFS=$SEP read -r id app track cork mute pid binary activity; do
        [[ -n $id ]] || continue
        [[ $pid =~ ^[0-9]+$ ]] || pid=''
        if [[ -z $binary && -n $pid ]]; then
            IFS= read -r binary 2>/dev/null < "/proc/$pid/comm" || true
        fi
        app=${app:-${binary:-Unknown}}
        ids+=("$id"); apps+=("$app"); media+=("$track"); corked+=("$cork")
        muted+=("$mute"); pids+=("$pid"); binaries+=("$binary"); audible+=("$activity")
        if [[ -n $pid ]]; then pid_counts[$pid]=$(( ${pid_counts[$pid]:-0} + 1 )); fi
    done <<< "$raw"
    _update_players
    for i in "${!ids[@]}"; do
        _detect_backend "$i"; backends+=("$REPLY")
    done
    # Loaded MPRIS and CLI sessions remain resumable without an audio stream.
    # Prefer a single interface when both describe the same player.
    local player backend app binary controller represented session_title session_artist session_status
    local -a sessions=()
    for player in "${players[@]}"; do
        sessions+=("mpris:$player")
    done
    for controller in "$CONTROLLERS_DIR"/*; do
        [[ -f $controller && -x $controller ]] || continue
        sessions+=("cli:${controller##*/}")
    done
    for backend in "${sessions[@]}"; do
        [[ ! ${used_backends[$backend]+present} ]] || continue
        case $backend in
            mpris:*)
                player=${backend#mpris:}; binary=${player%%.*}; pid=${player_pids[$player]}
                represented=0
                if [[ $pid =~ ^[0-9]+$ && ${used_backends[cli:${binary,,}]+present} ]]; then
                    for i in "${!ids[@]}"; do
                        if [[ ${backends[i]} == "cli:${binary,,}" && ${pids[i]} == "$pid" ]]; then
                            represented=1; break
                        fi
                    done
                fi
                ((represented == 0)) || continue
                ;;
            cli:*)
                binary=${backend#cli:}; pid=''
                ;;
        esac
        _backend_metadata "$backend"
        [[ -n $title || -n $artist ]] || continue
        case ${status,,} in playing|paused|stopped) ;; *) continue ;; esac
        if [[ $backend == cli:* ]]; then
            # Without a stream PID, compare loaded media rather than app names alone.
            session_title=$title; session_artist=$artist; session_status=$status; represented=0
            for player in "${players[@]}"; do
                [[ ${player%%.*} == "$binary" && ${used_backends[mpris:$player]+present} ]] || continue
                _backend_metadata "mpris:$player"
                if [[ $title == "$session_title" && $artist == "$session_artist" && $status == "$session_status" ]]; then
                    represented=1; break
                fi
            done
            ((represented == 0)) || continue
            _backend_metadata "$backend"
        fi
        app=$binary
        for i in "${!ids[@]}"; do
            if [[ -n ${pids[i]} && ${pids[i]} == "$pid" ]] ||
                [[ ${binaries[i],,} == "${binary,,}" ]]; then
                app=${apps[i]}
                break
            fi
        done
        [[ $pid =~ ^[0-9]+$ ]] || pid=''
        ids+=("$backend"); apps+=("$app"); media+=("$title")
        corked+=(yes); muted+=(no); pids+=("$pid"); binaries+=("$binary")
        backends+=("$backend"); audible+=(no); used_backends[$backend]=1
    done
    # Keep the controllable media session plus every additional audible stream.
    # Failed measurements are inconclusive: retain uncorked streams rather than
    # silently losing real playback when a monitor cannot be sampled.
    for i in "${!ids[@]}"; do
        if [[ ${backends[i]} == pactl ]]; then
            if [[ ${audible[i]} == no || ${corked[i]} == yes || ${muted[i]} == yes ]]; then
                unset 'ids[i]' 'apps[i]' 'media[i]' 'corked[i]' 'muted[i]' \
                    'pids[i]' 'binaries[i]' 'backends[i]' 'audible[i]'
                continue
            fi
        else
            _backend_metadata "${backends[i]}"
            [[ -z $title ]] || media[i]=$title
        fi
        _build_entry "$i"; entries[i]=$REPLY
        app=${apps[i]}
        app_counts[$app]=$(( ${app_counts[$app]:-0} + 1 ))
    done
    if ((${#ids[@]} == 0)); then
        _notify 'No Audio Sources' 'No active audio streams or loaded media sessions were found.'
    fi
}

_control_source() {
    local action=$1 backend=$2
    case $backend in
        mpris:*)
            case $action in
                toggle) action=PlayPause ;; next) action=Next ;; prev) action=Previous ;; stop) action=Stop ;;
                *) return 1 ;;
            esac
            busctl --user --quiet --timeout=2 --auto-start=no call \
                "org.mpris.MediaPlayer2.${backend#mpris:}" /org/mpris/MediaPlayer2 \
                org.mpris.MediaPlayer2.Player "$action"
            ;;
        cli:*) timeout --kill-after=1 2 "$CONTROLLERS_DIR/${backend#cli:}" "$action" ;;
        *) return 1 ;;
    esac
}

_perform_action() {
    local action=$1 i=$2
    if ! _control_source "$action" "${backends[i]}"; then
        _notify 'Playback Control Failed' "${apps[i]}: $action failed or is unsupported."
        return 1
    fi
    # Let asynchronous players publish their new status before notifying.
    case $action in toggle) sleep 0.2 ;; *) sleep 0.4 ;; esac
    unset 'metadata_cache[${backends[i]}]'
    _read_metadata "$i" backend
    _status_icon "$status"
    _notify "${apps[i]}" "$REPLY $title${artist:+ · $artist} ($status)"
}

show_control_menu() {
    local i=$1 label rc ROFI_MESSAGE=''
    if [[ ${backends[i]} == pactl ]]; then
        ROFI_MESSAGE='Playback controls are unavailable for this stream.'
    fi
    while :; do
        local -a controls=("$ICON_BACK  Back to Sources")
        if [[ ${backends[i]} != pactl ]]; then
            unset 'metadata_cache[${backends[i]}]'
            _read_metadata "$i"
            case ${status,,} in playing) label=Pause ;; paused) label=Play ;; *) label=Play/Pause ;; esac
            controls=("$ICON_TOGGLE  $label" "$ICON_NEXT  Next Track"
                "$ICON_PREV  Previous Track" "$ICON_BACK  Back to Sources")
        fi
        rc=0
        _rofi_menu "${apps[i]}" "${controls[@]}" || rc=$?
        ((rc == 0)) || return "$((rc == 1 ? 0 : rc))"
        [[ ${backends[i]} != pactl ]] || return 0
        case $REPLY in
            0) _perform_action toggle "$i" || true ;;
            1) _perform_action next "$i" || true ;;
            2) _perform_action prev "$i" || true ;;
            *) return 0 ;;
        esac
    done
}

show_source_menu() {
    local group='' app i rc selected
    while :; do
        _load_sources || return 1
        ((${#ids[@]})) || return 0
        local -a rows=() source_map=() group_map=()
        local -A seen=()
        if [[ -n $group && ! ${app_counts[$group]+present} ]]; then group=''; fi
        for i in "${!ids[@]}"; do
            app=${apps[i]}
            if [[ -n $group ]]; then
                [[ $app == "$group" ]] || continue
                rows+=("${entries[i]}"); source_map+=("$i"); group_map+=('')
            elif [[ ! ${seen[$app]+present} ]]; then
                seen[$app]=1
                if ((${app_counts[$app]} > 1)); then
                    rows+=("$ICON_GROUP  $app  (${app_counts[$app]} sources)")
                    source_map+=(-1); group_map+=("$app")
                else
                    rows+=("${entries[i]}"); source_map+=("$i"); group_map+=('')
                fi
            fi
        done
        if [[ -n $group ]]; then
            rows+=("$ICON_BACK  Back to Sources"); source_map+=(-2); group_map+=('')
        fi
        rc=0
        _rofi_menu "${group:-Audio} Sources" "${rows[@]}" || rc=$?
        if ((rc == 1)); then
            [[ -n $group ]] || return 0
            group=''; continue
        elif ((rc != 0)); then return "$rc"
        fi
        selected=${source_map[REPLY]}
        case $selected in
            -1) group=${group_map[REPLY]} ;;
            -2) group='' ;;
            *) show_control_menu "$selected" || return $? ;;
        esac
    done
}

cli_action() {
    local action=$1 i fallback=''
    _load_sources || return 1
    ((${#ids[@]})) || return 0
    for i in "${!ids[@]}"; do
        [[ ${backends[i]} != pactl ]] || continue
        [[ -n $fallback ]] || fallback=$i
        _read_metadata "$i"
        if [[ ${status,,} == playing ]]; then
            _perform_action "$action" "$i"
            return $?
        fi
    done
    if [[ -n $fallback ]]; then _perform_action "$action" "$fallback"
    else
        _notify 'No Playback Controls' 'The current audio streams have no playback interface.'
        return 1
    fi
}

cli_status() {
    _load_sources || return 1
    ((${#ids[@]})) || return 0
    local i body=''
    for i in "${!ids[@]}"; do body+="${entries[i]}"$'\n'; done
    _notify 'Media Sources' "${body%$'\n'}"
}

usage() {
    cat <<EOF_HELP
Usage: ${0##*/} [OPTION]

With no arguments, open the Rofi audio source menu.
Lists media sessions and additional streams with measured audio activity.
Paused media stays resumable. Silent idle connections are hidden.
Playback controls require a matching MPRIS/CLI interface.

  --toggle      Play/Pause the first playing controllable source, or first controllable source
  --next        Skip to its next track
  --prev        Skip to its previous track
  --status      Show available media sessions via notification
  -h, --help    Show this help
EOF_HELP
}

main() {
    if (($# > 1)); then usage >&2; return 1; fi
    case ${1:-} in
        -h|--help) usage; return 0 ;;
        ''|--toggle|--next|--prev|--status) ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; return 1 ;;
    esac
    local cmd
    for cmd in pactl jq notify-send python3 parec timeout; do
        command -v "$cmd" >/dev/null || { printf '%s: missing dependency: %s\n' "$APP_NAME" "$cmd" >&2; return 1; }
    done
    if [[ -z ${1:-} ]]; then
        command -v rofi >/dev/null || { printf '%s: missing dependency: rofi\n' "$APP_NAME" >&2; return 1; }
    fi
    if command -v busctl >/dev/null; then HAS_MPRIS=1; fi
    case ${1:-} in
        --toggle) cli_action toggle ;;
        --next) cli_action next ;;
        --prev) cli_action prev ;;
        --status) cli_status ;;
        '') show_source_menu ;;
    esac
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
