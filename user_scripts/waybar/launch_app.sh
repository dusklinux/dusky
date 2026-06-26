#!/usr/bin/env bash

# Helper script for Waybar to launch user-selected default applications dynamically
# based on active choices from Dusky TUI Switchers.

set -euo pipefail

# Helper to read state files or fallback to default_apps.lua or defaults
get_setting() {
    local setting_name="$1"
    local fallback_val="$2"
    local smart_file="$HOME/.config/dusky/settings/${setting_name}_switch.smart"
    
    if [[ -f "$smart_file" ]]; then
        cat "$smart_file" | tr -d '\r\n[:space:]'
    else
        # Try parsing from default_apps.lua if it exists
        local lua_file="$HOME/.config/hypr/edit_here/source/default_apps.lua"
        if [[ -f "$lua_file" ]]; then
            local val
            val=$(grep -i -E "^[[:space:]]*${setting_name}[[:space:]]*=" "$lua_file" | tail -n 1 | sed -E 's/--.*//' | cut -d'=' -f2 | sed -E 's/\r//g; s/^[[:space:]]*//; s/[[:space:]]*$//; s/^["'\'']//; s/["'\'']$//')
            if [[ -n "$val" ]]; then
                echo "$val"
                return
            fi
        fi
        echo "$fallback_val"
    fi
}

get_is_terminal() {
    local setting_name="$1"
    local fallback_val="$2"
    local legacy_file="$HOME/.config/dusky/settings/${setting_name}_switch"
    
    if [[ -f "$legacy_file" ]]; then
        cat "$legacy_file" | tr -d '\r\n[:space:]'
    else
        echo "$fallback_val"
    fi
}

# Determine terminal
TERMINAL=$(get_setting "terminal" "kitty")

APP_TYPE="${1:-}"

case "$APP_TYPE" in
    terminal)
        exec uwsm-app -- "$TERMINAL"
        ;;
    browser)
        BROWSER=$(get_setting "browser" "firefox")
        IS_TERM=$(get_is_terminal "browser" "false")
        if [[ "$IS_TERM" == "true" ]]; then
            exec uwsm-app -- "$TERMINAL" "$BROWSER"
        else
            exec uwsm-app -- "$BROWSER"
        fi
        ;;
    file-manager)
        FM=$(get_setting "filemanager" "thunar")
        IS_TERM=$(get_is_terminal "filemanager" "false")
        if [[ "$IS_TERM" == "true" ]]; then
            exec uwsm-app -- "$TERMINAL" "$FM"
        else
            exec uwsm-app -- "$FM"
        fi
        ;;
    text-editor)
        EDITOR=$(get_setting "texteditor" "gnome-text-editor")
        IS_TERM=$(get_is_terminal "texteditor" "false")
        if [[ "$IS_TERM" == "true" ]]; then
            TERM_LOWER="${TERMINAL,,}"
            if [[ "$TERM_LOWER" == *"kitty"* ]]; then
                exec uwsm-app -- kitty --class "$EDITOR" "$EDITOR"
            elif [[ "$TERM_LOWER" == *"foot"* ]]; then
                exec uwsm-app -- foot --app-id="$EDITOR" "$EDITOR"
            elif [[ "$TERM_LOWER" == *"alacritty"* ]]; then
                exec uwsm-app -- alacritty --class "$EDITOR" -e "$EDITOR"
            elif [[ "$TERM_LOWER" == *"wezterm"* ]]; then
                exec uwsm-app -- wezterm start --class "$EDITOR" -- "$EDITOR"
            else
                exec uwsm-app -- "$TERMINAL" "$EDITOR"
            fi
        else
            exec uwsm-app -- "$EDITOR"
        fi
        ;;
    *)
        echo "Usage: $0 {terminal|browser|file-manager|text-editor}" >&2
        exit 1
        ;;
esac
