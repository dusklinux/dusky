#!/usr/bin/env bash

CTL_FILE="$HOME/.cache/dusky/visualizer.ctl"
mkdir -p "$(dirname "$CTL_FILE")"
echo "overlay" > "$CTL_FILE"
