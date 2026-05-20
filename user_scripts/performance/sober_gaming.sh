#!/bin/bash

GAMING_SCRIPT="$HOME/user_scripts/performance/gaming_mode.sh"
SOBER="org.vinegarhq.Sober"

"$GAMING_SCRIPT" on

flatpak run --branch=stable --arch=x86_64 --command=sober --file-forwarding "$SOBER" -- "$@"

"$GAMING_SCRIPT" off
