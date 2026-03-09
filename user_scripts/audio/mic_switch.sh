#!/usr/bin/env bash
# Requires Bash 5.3.0 or higher
# -----------------------------------------------------------------------------
# OPTIMIZED MICROPHONE INPUT SWITCHER FOR HYPRLAND
# Dependencies: hyprland, pulseaudio-utils (pactl), jq, swayosd-client
# -----------------------------------------------------------------------------
set -euo pipefail

# 1. Elite DevOps Bash 5.3+ Check
if (( BASH_VERSINFO[0] < 5 || (BASH_VERSINFO[0] == 5 && BASH_VERSINFO[1] < 3) )); then
    printf -- "Error: This script leverages Bash 5.3 non-forking command substitutions. Upgrade your shell.\n" >&2
    exit 1
fi

# 2. Dependency check
for cmd in pactl jq hyprctl swayosd-client; do
    if ! command -v "$cmd" &>/dev/null; then
        printf "Error: Required command '%s' not found.\n" "$cmd" >&2
        exit 1
    fi
done

# 3. Get the currently focused monitor for OSD notification (Bash 5.3 Non-forking)
FOCUSED_MONITOR=${ hyprctl monitors -j | jq -r '.[] | select(.focused == true).name // empty'; }

# 4. Get the current default source
CURRENT_SOURCE=${ pactl get-default-source 2>/dev/null || echo ""; }

# 5. THE LOGIC CORE
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

# 6. Error handling: No sources found
if [[ -z "$SOURCE_DATA" ]]; then
    swayosd-client ${FOCUSED_MONITOR:+--monitor "$FOCUSED_MONITOR"} \
        --custom-message "No Input Devices Available" \
        --custom-icon "microphone-sensitivity-muted-symbolic"
    exit 1
fi

# 7. Parse the output safely
IFS=$'\t' read -r NEXT_NAME NEXT_DESC NEXT_VOL NEXT_MUTE <<< "$SOURCE_DATA"

# 8. Ensure volume is numeric (fallback to 0)
if ! [[ "$NEXT_VOL" =~ ^[0-9]+$ ]]; then
    NEXT_VOL=0
fi

# 9. Switch the default source
if ! pactl set-default-source "$NEXT_NAME" 2>/dev/null; then
    swayosd-client ${FOCUSED_MONITOR:+--monitor "$FOCUSED_MONITOR"} \
        --custom-message "Failed to switch input" \
        --custom-icon "dialog-error-symbolic"
    exit 1
fi

# 10. Move all currently recording applications to the new source
# Using a process substitution loop to avoid subshell variable scoping issues
while IFS=$'\t' read -r output_id _; do
    if [[ -n "$output_id" ]]; then
        pactl move-source-output "$output_id" "$NEXT_NAME" 2>/dev/null || true
    fi
done < <(pactl list short source-outputs 2>/dev/null)

# 11. Determine icon based on volume and mute status
if [[ "$NEXT_MUTE" == "true" ]] || (( NEXT_VOL == 0 )); then
    ICON="microphone-sensitivity-muted-symbolic"
elif (( NEXT_VOL <= 33 )); then
    ICON="microphone-sensitivity-low-symbolic"
elif (( NEXT_VOL <= 66 )); then
    ICON="microphone-sensitivity-medium-symbolic"
else
    ICON="microphone-sensitivity-high-symbolic"
fi

# 12. Display the OSD notification
swayosd-client \
    ${FOCUSED_MONITOR:+--monitor "$FOCUSED_MONITOR"} \
    --custom-message "${NEXT_DESC:-Unknown Device}" \
    --custom-icon "$ICON"

exit 0
