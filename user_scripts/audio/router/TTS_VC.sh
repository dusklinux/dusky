#!/bin/bash

pkill rofi || true

TEXT=$(rofi -dmenu -p "TTS Text:")
[ -z "$TEXT" ] && exit 1

echo -n "$TEXT" | wl-copy

~/user_scripts/audio/router/audio_routing_output_to_mic.py --daemon mpv &

sleep 1

~/user_scripts/tts_stt/dusky_kokoro/trigger.sh

while ! pgrep -x mpv >/dev/null; do
	sleep 0.5
done

while pgrep -x mpv >/dev/null; do
	sleep 1
done

~/user_scripts/audio/router/audio_routing_output_to_mic.py --stop
