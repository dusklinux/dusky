#!/usr/bin/env bash

LOCK_FILE="/tmp/grimblast-lock"

# 1. ATOMIC LOCK CHECK
# Open the lock file. If it's busy (spamming), exit immediately.
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    exit 1
fi

# 2. DEFINE THE ACTION
# We perform the screenshot, then sleep, THEN release the lock.

if [ "$1" == "swappy" ]; then
    # --- SWAPPY MODE ---
    TEMP_FILE="/tmp/screenshot-$(date +%s).png"

    # Capture (Lock held)
    # We close FD 200 for grimblast to be safe
    grimblast --freeze save area "$TEMP_FILE" 200>&-
    
    # Force the script to hold the lock for 0.2 second after selection.
    # This prevents you from starting a new screenshot too fast.
    sleep 0.2

    # Release lock
    flock -u 200

    # Open Swappy (Background)
    if [ -s "$TEMP_FILE" ]; then
        dusky-run -- swappy -f "$TEMP_FILE" &
    fi

else
    # --- CLIPBOARD MODE ---
    
    # Capture (Lock held)
    grimblast --freeze copy area 200>&-
    
    sleep 0.2
    
    # Release lock
    flock -u 200
fi
