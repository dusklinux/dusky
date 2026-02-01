#!/usr/bin/env bash
# ==============================================================================
# Script: 003_mouse_button_reverse.sh
# Purpose: Checks status and toggles mouse handedness in Hyprland
# ==============================================================================

set -euo pipefail

# --- Configuration ---
readonly CONFIG_FILE="${HOME}/.config/hypr/edit_here/source/input.conf"

# --- Cleanup Trap ---
cleanup() {
    [[ -f "${TEMP_FILE:-}" ]] && rm -f "$TEMP_FILE" || true
}
trap cleanup EXIT

# --- Main ---
main() {
    # Ensure file exists
    if [[ ! -f "$CONFIG_FILE" ]]; then
        mkdir -p "$(dirname "$CONFIG_FILE")"
        printf "input {\n}\n" > "$CONFIG_FILE"
    fi

    # --- 1. Detect Current State ---
    # Default assumption: Right-Handed (false)
    local current_mode="Right-Handed (Standard)"
    local target_val="true"
    local prompt_action="Switch to Left-Handed (Reverse)"

    # regex checks for 'left_handed = true' allowing for flexible whitespace
    if grep -qE '^[[:space:]]*left_handed[[:space:]]*=[[:space:]]*true' "$CONFIG_FILE"; then
        current_mode="Left-Handed (Reversed)"
        target_val="false"
        prompt_action="Switch to Right-Handed (Standard)"
    fi

    # --- 2. Prompt User ---
    printf "Current Status: %s\n" "$current_mode"
    # Changed prompt to [Y/n] to indicate Yes is default
    printf "%s? [Y/n]: " "$prompt_action"
    
    # Using /dev/tty ensures we read from the user even if stdin is redirected elsewhere
    read -r -n 1 user_input < /dev/tty
    printf "\n"

    # --- 3. Process Logic ---
    # Check for Y, y, OR empty string (-z checks for Enter key)
    if [[ "$user_input" =~ ^[Yy]$ ]] || [[ -z "$user_input" ]]; then
        
        # Atomic Parse & Write
        TEMP_FILE=$(mktemp)
        
        awk -v target_val="$target_val" '
        BEGIN { inside_input = 0; modified = 0 }
        
        # Detect start of input block
        /^input[[:space:]]*\{/ { 
            inside_input = 1
            print $0
            next 
        }
        
        # Detect end of input block
        inside_input && /^\}/ {
            if (modified == 0) {
                print "    left_handed = " target_val
                modified = 1
            }
            inside_input = 0
            print $0
            next
        }
        
        # Detect existing key inside input block
        inside_input && /^[[:space:]]*left_handed[[:space:]]*=/ {
            sub(/=.*/, "= " target_val)
            modified = 1
            print $0
            next
        }
        
        { print }
        ' "$CONFIG_FILE" > "$TEMP_FILE"

        # Move new config into place (Atomic)
        mv "$TEMP_FILE" "$CONFIG_FILE"

        # Silent Reload if Hyprland is active
        if pgrep -x "Hyprland" > /dev/null; then
            command -v hyprctl >/dev/null && hyprctl reload > /dev/null 2>&1 || true
        fi

        # Output Success Message
        printf "Success: Configuration updated to %s (left_handed = %s).\n" "${prompt_action%% *}" "$target_val"
    else
        printf "No changes made.\n"
    fi
}

main
