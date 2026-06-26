#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Graceful Browser Restarter (Test Script)
# Target: Arch Linux / Hyprland / Wayland
# -----------------------------------------------------------------------------

set -euo pipefail

# Check for the running browser based on your priority list
declare -a BROWSERS=("firefox" "zen-bin" "zen" "librewolf")
declare TARGET=""

for b in "${BROWSERS[@]}"; do
    if pgrep -x "$b" > /dev/null; then
        TARGET="$b"
        break
    fi
done

if [[ -z "$TARGET" ]]; then
    echo "[-] No supported browser is currently running."
    exit 1
fi

echo "[*] Gracefully stopping $TARGET..."
# SIGTERM (-15) asks the browser to close cleanly and save its session/tabs
pkill -15 -x "$TARGET"

# Wait for the process to fully exit to prevent profile lock errors
while pgrep -x "$TARGET" > /dev/null; do
    sleep 0.1
done

echo "[*] $TARGET closed successfully. Reopening immediately..."

# Launch the browser detached from the terminal so the script can exit
nohup "$TARGET" >/dev/null 2>&1 &
disown

echo "[+] Done. Browser restarted with new CSS applied."
