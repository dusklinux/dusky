#!/usr/bin/env bash

DAEMON_SCRIPT="$HOME/user_scripts/way_layers/visualizer/visualizer_daemon.py"
CTL_FILE="$HOME/.cache/dusky/visualizer.ctl"

mkdir -p "$(dirname "$CTL_FILE")"

if ! pgrep -f "visualizer_daemon.py" > /dev/null; then
    python "$DAEMON_SCRIPT" &
    disown
else
    echo "toggle" > "$CTL_FILE"
fi
