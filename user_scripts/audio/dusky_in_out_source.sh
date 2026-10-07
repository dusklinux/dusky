#!/usr/bin/env bash
# PipeWire/WirePlumber audio selector for Linux 7.3+ and Wayland.
set -euo pipefail

readonly ROFI_THEME_STR='window { width: 460px; } listview { lines: 6; }'
readonly SYNC_ID='sys-osd'
script_dir=${BASH_SOURCE[0]}
if [[ $script_dir == */* ]]; then
    script_dir=${script_dir%/*}
else
    script_dir=.
fi
readonly STUDIO_BIN="$script_dir/dusky_audio_studio/dusky_audio_studio.py"

notify() {
    if command -v notify-send &>/dev/null; then
        notify-send "$@" || :
    fi
}

die() {
    printf 'Dusky Audio: %s\n' "$*" >&2
    notify -u critical -- 'Dusky Audio' "$*"
    exit 1
}

require_commands() {
    local cmd
    for cmd; do
        command -v "$cmd" &>/dev/null || die "Missing required dependency: $cmd"
    done
}

# Return the original row index; cancellation is separate from menu failure.
select_menu() {
    local -n selected=$1
    local prompt=$2 status
    shift 2
    if selected=$(printf '%s\n' "$@" | rofi -dmenu -i -no-custom -p "$prompt" \
        -theme-str "$ROFI_THEME_STR" -format i); then
        [[ $selected =~ ^(0|[1-9][0-9]*)$ ]] || die 'Invalid menu selection.'
        (( selected >= 0 && selected < $# )) || die 'Menu selection is out of range.'
        return 0
    else
        status=$?
        case $status in
            1|130|143) return 1 ;;
            *) die "Audio menu failed (exit status $status)." ;;
        esac
    fi
}

run_studio() {
    require_commands python3
    [[ -f $STUDIO_BIN ]] || die "Audio Studio was not found: $STUDIO_BIN"
    case $1 in
        toggle) python3 "$STUDIO_BIN" --toggle ;;
        gui) python3 "$STUDIO_BIN" --gui & ;;
    esac
}

menu_devices() {
    require_commands wpctl pw-dump jq awk
    local kind=$1 media_class default_key prompt icon muted_icon osd_prefix
    case $kind in
        output)
            media_class='Audio/Sink' default_key='default.audio.sink'
            prompt='󰓃  Select Output' icon=' ' muted_icon=' '
            osd_prefix='audio-volume'
            ;;
        input)
            media_class='Audio/Source' default_key='default.audio.source'
            prompt='  Select Input' icon=' ' muted_icon=' '
            osd_prefix='microphone-sensitivity'
            ;;
    esac

    local snapshot rows
    snapshot=$(pw-dump --no-colors) || die 'Could not query PipeWire devices.'
    # Names, mute flags and the effective default come from one coherent snapshot.
    rows=$(jq -c --arg class "$media_class" --arg key "$default_key" '
        ([.[] | select(.type == "PipeWire:Interface:Metadata" and
                        .props["metadata.name"] == "default")
          | .metadata[]? | select(.subject == 0 and .key == $key) | .value.name][0] // "") as $default
        | [.[] | select(.type == "PipeWire:Interface:Node")
           | select(.info.props["media.class"] == $class)
           | .info.props as $p
           | ($p["node.name"] // "") as $name
           | (if ["ghelper-audio-sink", "ghelper-audio-sink-out",
                  "dusky-audio-sink", "dusky-audio-sink-out"] | index($name) then "Dusky Audio"
              elif ["ghelper-audio", "ghelper-audio-capture",
                    "dusky-audio", "dusky-audio-capture"] | index($name) then "Dusky Mic"
              else [$p["node.nick"], $p["node.description"], $name]
                   | map(select(type == "string" and length > 0)) | .[0] // "Unnamed device"
              end
              | gsub(" \\((Two-Way RT DSP|Noise Suppressed)\\)"; "")
              | gsub("[\u0000-\u001f\u007f]"; " ")
              | gsub("^\\s+|\\s+$"; "")) as $label
           | {id, serial: $p["object.serial"], label: $label,
              name: $name, active: ($name == $default), muted: any(.info.params.Props[]?; .mute == true)}]
        | sort_by(.label, .id)
    ' <<< "$snapshot") || die 'Could not parse PipeWire devices.'

    local -a ids=() serials=() names=() node_names=() options=()
    local -A used_options=()
    local id serial name node_name active muted display base_display suffix
    while IFS= read -r -d '' id && IFS= read -r -d '' serial &&
          IFS= read -r -d '' name && IFS= read -r -d '' node_name &&
          IFS= read -r -d '' active &&
          IFS= read -r -d '' muted; do
        display="$icon  $name"
        [[ $muted == true ]] && display="$muted_icon  $name"
        [[ $active == true ]] && display+='  [Active]'
        base_display=$display suffix=2
        while [[ ${used_options[$display]+present} ]]; do
            display="$base_display ($suffix)"
            ((suffix += 1))
        done
        used_options[$display]=1
        ids+=("$id") serials+=("$serial") names+=("$name")
        node_names+=("$node_name") options+=("$display")
    done < <(jq --raw-output0 '.[] | (.id | tostring), (.serial | tostring),
                             .label, .name, (.active | tostring), (.muted | tostring)' <<< "$rows")

    ((${#ids[@]})) || die "No $kind devices found."
    if [[ ${DUSKY_AUDIO_TEST:-0} == 1 ]]; then
        printf '%s\n' "${options[@]}"
        return 0
    fi

    require_commands rofi
    local choice
    select_menu choice "$prompt" "${options[@]}" || return 0
    local target_id=${ids[choice]} target_serial=${serials[choice]}
    if [[ $kind == output && ${node_names[choice]} != mono_global_downmix ]] &&
       jq -e 'any(.[]; .name == "mono_global_downmix")' <<< "$rows" >/dev/null; then
        # Choosing a physical output already bypasses mono. Remove its owned graph
        # first so the indicator and the next mono toggle reflect that change.
        require_commands python3
        local mono_bin="$script_dir/mono_audio_pipewire.py"
        [[ -f $mono_bin ]] || die "Mono audio helper was not found: $mono_bin"
        python3 "$mono_bin" disable || die 'Could not leave mono mode before switching output.'
    fi
    # Global node IDs are reused after hotplug; reject a stale menu selection.
    snapshot=$(pw-dump --no-colors "$target_id") || die 'The selected device is no longer available.'
    jq -e --argjson id "$target_id" --argjson serial "$target_serial" --arg class "$media_class" '
        any(.[]; .type == "PipeWire:Interface:Node" and .id == $id and
                 .info.props["object.serial"] == $serial and .info.props["media.class"] == $class)
    ' <<< "$snapshot" >/dev/null || die 'The selected device changed; reopen the audio menu.'
    wpctl set-default "$target_id" || die 'Could not set the selected device as default.'

    local vol_info vol_val vol_pct osd_icon="$osd_prefix-high-symbolic"
    if vol_info=$(LC_ALL=C wpctl get-volume "$target_id") &&
       [[ $vol_info =~ ^Volume:[[:space:]]+([0-9]+([.][0-9]+)?) ]]; then
        vol_val=${BASH_REMATCH[1]}
        vol_pct=$(LC_ALL=C awk -v v="$vol_val" 'BEGIN { printf "%.0f", v * 100 }')
        if [[ $vol_info == *'[MUTED]'* ]] || ((vol_pct == 0)); then
            osd_icon="$osd_prefix-muted-symbolic"
        elif ((vol_pct <= 33)); then
            osd_icon="$osd_prefix-low-symbolic"
        elif ((vol_pct <= 66)); then
            osd_icon="$osd_prefix-medium-symbolic"
        fi
        notify -a OSD -h "string:x-canonical-private-synchronous:$SYNC_ID" \
            -h "int:value:$vol_pct" -i "$osd_icon" -- "${names[choice]}"
    else
        # The routing change succeeded; do not invent a volume if the node vanished.
        notify -a OSD -i "$osd_icon" -- "${names[choice]}"
    fi
}

menu_main() {
    require_commands rofi pgrep
    local dsp_status=Off choice
    if pgrep --uid "$UID" --exact dusky_audio_dsp &>/dev/null; then
        dsp_status=On
    fi
    local -a options=(
        '󰓃  Playback Output Devices'
        '  Microphone Input Devices'
        "󰔏  Toggle Dusky Audio DSP  [$dsp_status]"
        '  Open Dusky Audio Studio'
    )
    select_menu choice '󰕾  Dusky Audio' "${options[@]}" || return 0
    case $choice in
        0) menu_devices output ;;
        1) menu_devices input ;;
        2) run_studio toggle ;;
        3) run_studio gui ;;
    esac
}

(($# <= 1)) || die 'Expected at most one action. Use --help for usage.'
case ${1:-} in
    -o|--output|output|out) menu_devices output ;;
    -i|--input|input|in) menu_devices input ;;
    -t|--toggle|toggle) run_studio toggle ;;
    -s|--studio|studio|gui) run_studio gui ;;
    -h|--help|help)
        printf '%s\n' 'Usage: dusky_in_out_source.sh [FLAG]' '' \
            '  -o, --output      Select playback output' \
            '  -i, --input       Select microphone input' \
            '  -t, --toggle      Toggle Dusky Audio DSP' \
            '  -s, --studio      Launch Dusky Audio Studio GUI' \
            '  -h, --help        Show this help' '' \
            'With no arguments, open the Dusky Audio master menu.'
        ;;
    '') menu_main ;;
    *) die "Unknown action: $1. Use --help for usage." ;;
esac
