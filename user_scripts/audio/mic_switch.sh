#!/usr/bin/env bash
# Requires Bash 5.3.0 or higher
# -----------------------------------------------------------------------------
# OPTIMIZED MICROPHONE INPUT SWITCHER FOR HYPRLAND (MAKO OSD EDITION)
# Dependencies: hyprland, pulseaudio-utils (pactl), jq, libnotify (notify-send)
# -----------------------------------------------------------------------------
set -euo pipefail

SYNC_ID="sys-osd"

# Core notification wrapper for Mako
notify() {
    local icon="$1"
    local title="$2"
    local val="${3:-}"
    
    if [[ -n "$val" ]]; then
        notify-send -a "OSD" -h string:x-canonical-private-synchronous:"$SYNC_ID" -h int:value:"$val" -i "$icon" "$title"
    else
        notify-send -a "OSD" -h string:x-canonical-private-synchronous:"$SYNC_ID" -i "$icon" "$title"
    fi
}

# 1. Elite DevOps Bash 5.3+ Check
if (( BASH_VERSINFO[0] < 5 || (BASH_VERSINFO[0] == 5 && BASH_VERSINFO[1] < 3) )); then
    printf -- "Error: This script leverages Bash 5.3 non-forking command substitutions. Upgrade your shell.\n" >&2
    exit 1
fi

# 2. Dependency check
for cmd in pactl jq notify-send; do
    if ! command -v "$cmd" &>/dev/null; then
        printf "Error: Required command '%s' not found.\n" "$cmd" >&2
        exit 1
    fi
done

# 3. Get the current default source (Bash 5.3 Non-forking)
CURRENT_SOURCE=${ pactl get-default-source 2>/dev/null || echo ""; }

# 4. THE LOGIC CORE
# Filter out monitor sources, check availability, sort, and extract TSV payload
SOURCE_DATA=${ pactl -f json list sources 2>/dev/null | jq -r --arg current "$CURRENT_SOURCE" '
  [ .[]
    | select(.monitor_of == null)
    | select((.ports | length == 0) or ([.ports[]? | .availability != "not available"] | any))
  ]
  | sort_by(.name) as $sources
  | ($sources | length) as $len

  | if $len == 0 then ""
    else
      (($sources | map(.name) | index($current)) // -1) as $idx
      | (if $idx < 0 then 0 else ($idx + 1) % $len end) as $next_idx
      | $sources[$next_idx]
      | [
          .name,
          ((.description // .properties."device.description" // .properties."node.description" // .properties."device.product.name" // .name) | gsub("[\\t\\n\\r]"; " ")),
          ((.volume | to_entries[0].value.value_percent // "0%") | sub("%$"; "")),
          (if .mute then "true" else "false" end)
        ]
      | @tsv
    end
'; }

# 5. Error handling: No sources found
if [[ -z "$SOURCE_DATA" ]]; then
    notify "microphone-sensitivity-muted-symbolic" "No Input Devices Available" ""
    exit 1
fi

# 6. Parse the output safely
IFS=$'\t' read -r NEXT_NAME NEXT_DESC NEXT_VOL NEXT_MUTE <<< "$SOURCE_DATA"

# 7. Ensure volume is numeric (fallback to 0)
if ! [[ "$NEXT_VOL" =~ ^[0-9]+$ ]]; then
    NEXT_VOL=0
fi

# 8. Switch the default source
if ! pactl set-default-source "$NEXT_NAME" 2>/dev/null; then
    notify "dialog-error-symbolic" "Failed to switch input" ""
    exit 1
fi

# 9. Move all currently recording applications to the new source
# Using a process substitution loop to avoid subshell variable scoping issues
while IFS=$'\t' read -r output_id _; do
    if [[ -n "$output_id" ]]; then
        pactl move-source-output "$output_id" "$NEXT_NAME" 2>/dev/null || true
    fi
done < <(pactl list short source-outputs 2>/dev/null)

# 10. Determine icon based on volume and mute status
if [[ "$NEXT_MUTE" == "true" ]] || (( NEXT_VOL == 0 )); then
    ICON="microphone-sensitivity-muted-symbolic"
elif (( NEXT_VOL <= 33 )); then
    ICON="microphone-sensitivity-low-symbolic"
elif (( NEXT_VOL <= 66 )); then
    ICON="microphone-sensitivity-medium-symbolic"
else
    ICON="microphone-sensitivity-high-symbolic"
fi

# 11. Display the OSD notification (passing volume for Mako bar support)
notify "$ICON" "${NEXT_DESC:-Unknown Device}" "$NEXT_VOL"

exit 0
