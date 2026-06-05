#!/usr/bin/env bash

CFG="$HOME/.config/screen_recorder/config.conf"
[[ -f "$CFG" ]] && source "$CFG"

window="${window:-screen}"
container="${container:-mp4}"
output_dir="${output_dir:-$HOME/Videos}"
fps="${fps:-60}"
codec="${codec:-auto}"
quality="${quality:-very_high}"
encoder="${encoder:-gpu}"
audio="${audio:-default_output}"

if [[ "$window" == "region" && -z "$region" ]]; then
    window=$(slurp -f "%wx%h+%x+%y" 2>/dev/null)
    [[ -z "$window" ]] && exit 1
fi

# Fixed: Uses -f to bypass the 15-character limitation bug[cite: 5]
if pidof gpu-screen-recorder > /dev/null; then
    pkill -SIGINT -f "^gpu-screen-recorder"
    notify-send -i media-playback-stop 'GSR' '⏹ Recording stopped'
    exit 0
fi

args=(gpu-screen-recorder -w "$window" -c "$container" -f "$fps" -k "$codec" -q "$quality" -encoder "$encoder")

[[ -n "$region" && "$window" == "region" ]] && args+=(-region "$region")
[[ -n "$audio" && "$audio" != "none" ]] && args+=(-a "$audio")
[[ -n "$audio_codec" ]] && args+=(-ac "$audio_codec")
[[ -n "$audio_bitrate" ]] && args+=(-ab "$audio_bitrate")
[[ -n "$bitrate_mode" && "$bitrate_mode" != "none" ]] && args+=(-bm "$bitrate_mode")
[[ -n "$frame_mode" && "$frame_mode" != "none" ]] && args+=(-fm "$frame_mode")
[[ "$cursor" == "no" ]] && args+=(-cursor no)

if [[ -n "$replay_buffer" && "$replay_buffer" -gt 0 ]]; then
    args+=(-r "$replay_buffer")
    [[ -n "$replay_storage" ]] && args+=(-replay-storage "$replay_storage")
    [[ "$restart_replay" == "yes" ]] && args+=(-restart-replay-on-save yes)
    [[ "$dark_frame" == "yes" ]] && args+=(-df yes)
fi

OUTFILE="${output_dir}/Video_$(date +%Y-%m-%d_%H-%M-%S).${container}"
"${args[@]}" -o "$OUTFILE" > /tmp/gsr.log 2>&1 &

sleep 1
if ! kill -0 $! 2>/dev/null; then
    notify-send -u critical 'GSR Error' "Failed to start. Check /tmp/gsr.log"
else
    notify-send -i media-record 'GSR' '▶ Recording started'
fi
