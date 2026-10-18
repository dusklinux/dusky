#!/usr/bin/env bash
# Route this TTS request through an owned router, then tear it down.
set -euo pipefail

script_dir=$(cd -- "${BASH_SOURCE[0]%/*}" && pwd)
router="$script_dir/audio_routing_output_to_mic.py"
trigger="$script_dir/../../tts_stt/dusky_kokoro/trigger.sh"

text=$(rofi -dmenu -p 'TTS Text:' </dev/null) || {
    rc=$?
    ((rc == 1)) && exit 0
    exit "$rc"
}
[[ -n ${text//[[:space:]]/} ]] || exit 0

router_pid=''
tts_pid=''
# Invoked by the EXIT trap.
# shellcheck disable=SC2329
cleanup() {
    local result=$? router_result=0
    trap - EXIT
    if [[ -n $tts_pid ]]; then
        # The request client and its descendants run in their own session.
        kill -TERM -- "-$tts_pid" 2>/dev/null || kill -TERM "$tts_pid" 2>/dev/null || true
        wait "$tts_pid" 2>/dev/null || true
    fi
    if [[ -n $router_pid ]]; then
        kill -TERM "$router_pid" 2>/dev/null || true
        wait "$router_pid" 2>/dev/null || router_result=$?
        if ((router_result != 0)); then
            printf '%s\n' 'TTS routing daemon cleanup failed.' >&2
            ((result != 0)) || result=1
        fi
    fi
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

"$router" --daemon mpv &
router_pid=$!
deadline=$((SECONDS + 20))
ready=0
while ((SECONDS < deadline)); do
    if ! kill -0 "$router_pid" 2>/dev/null; then
        wait "$router_pid" || true
        printf '%s\n' 'TTS routing daemon failed to start.' >&2
        exit 1
    fi
    status=$("$router" --status 2>/dev/null) || status=''
    if [[ $status == *"PID      : $router_pid"$'\n'* && $status == *$'Phase    : ready\n'* ]]; then
        ready=1
        break
    fi
    sleep 0.1
done
if (( ! ready )); then
    printf '%s\n' 'TTS routing daemon did not become ready within 20 seconds.' >&2
    exit 1
fi

# The TTS client acknowledges completion, including multiple playback segments.
# Pass text directly so another clipboard owner cannot replace the request.
setsid "$trigger" --text "$text" --wait &
tts_pid=$!
completed_pid=''
rc=0
wait -n -p completed_pid "$router_pid" "$tts_pid" || rc=$?
if [[ ${completed_pid-} == "$router_pid" ]] || ! kill -0 "$router_pid" 2>/dev/null; then
    wait "$router_pid" 2>/dev/null || true
    router_pid=''
    printf '%s\n' 'TTS routing daemon stopped before the request completed.' >&2
    exit 1
fi
tts_pid=''
exit "$rc"
