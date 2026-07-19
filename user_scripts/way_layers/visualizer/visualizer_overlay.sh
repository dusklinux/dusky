#!/usr/bin/env bash

CTL_FILE="$HOME/.config/dusky/settings/way_layers/visualizer/visualizer.ctl"
mkdir -p "$(dirname "$CTL_FILE")"
echo "overlay" >"$CTL_FILE"
