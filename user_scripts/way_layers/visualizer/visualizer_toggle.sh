#!/usr/bin/env bash

DAEMON_SCRIPT="$HOME/user_scripts/way_layers/visualizer/visualizer_daemon.py"
CTL_FILE="$HOME/.config/dusky/settings/way_layers/visualizer/visualizer.ctl"

mkdir -p "$(dirname "$CTL_FILE")"

if ! systemctl --user is-active --quiet dusky_visualizer.service; then
    systemctl --user start dusky_visualizer.service
else
    echo "toggle" >"$CTL_FILE"
fi
