#!/usr/bin/env bash
# Force VS Code to reload the current theme by toggling it

# Get the current theme from VS Code settings
VSCODE_SETTINGS="$HOME/.config/Code/User/settings.json"

if [[ ! -f "$VSCODE_SETTINGS" ]]; then
    echo "VS Code settings not found"
    exit 1
fi

# Use code CLI to reload window
if command -v code &> /dev/null; then
    # Touch the theme files to trigger VS Code's file watcher
    touch ~/user_scripts/vscode/dms-theme/themes/dankshell-dark.json
    touch ~/user_scripts/vscode/dms-theme/themes/dankshell-light.json
    
    # Send notification
    notify-send -i preferences-desktop-theme "VS Code Theme" "Theme files updated - reload window to apply (Ctrl+Shift+P → Reload Window)"
fi
