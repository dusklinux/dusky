#!/usr/bin/env bash
# Spotify lives in its own special workspace (special:music). Bound to SUPER+SHIFT+M.
#
#   - No Spotify window  -> launch it, park it in special:music, show it.
#   - Window elsewhere   -> move it into special:music, show it.
#   - Already there      -> toggle special:music (show / hide).
#
# Hyprland 0.55+ (Lua config): `hyprctl dispatch` takes a Lua expression;
# the old `movetoworkspacesilent` / `togglespecialworkspace` strings fail.

set -uo pipefail

readonly WS="music"
readonly CLASS_RE='^(Spotify|spotify)$'

dsp() { hyprctl dispatch "$1" >/dev/null; }

# Workspace name of the Spotify window, or empty if there is none.
spotify_ws() {
    hyprctl clients -j 2>/dev/null |
        jq -r --arg re "$CLASS_RE" \
            'first(.[] | select((.class | test($re)) or (.initialClass | test($re)))) | .workspace.name // empty'
}

# Is special:music currently shown on the focused monitor?
special_visible() {
    [[ "$(hyprctl monitors -j | jq -r 'first(.[] | select(.focused)) | .specialWorkspace.name')" == "special:${WS}" ]]
}

current="$(spotify_ws)"

if [[ -z "$current" ]]; then
    # Launches Spotify, or raises a running instance that has no window.
    setsid -f spotify >/dev/null 2>&1
    # Spotify sets its window class late; wait for it (max ~10s).
    for _ in {1..100}; do
        current="$(spotify_ws)"
        [[ -n "$current" ]] && break
        sleep 0.1
    done
    if [[ -z "$current" ]]; then
        echo "spotify_toggle: Spotify window never appeared" >&2
        exit 1
    fi
fi

if [[ "$current" != "special:${WS}" ]]; then
    dsp "hl.dsp.window.move({ workspace = \"special:${WS}\", window = \"class:${CLASS_RE}\", follow = false })"
    special_visible || dsp "hl.dsp.workspace.toggle_special(\"${WS}\")"
else
    dsp "hl.dsp.workspace.toggle_special(\"${WS}\")"
fi
