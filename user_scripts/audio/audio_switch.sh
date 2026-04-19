#!/bin/bash
# -----------------------------------------------------------------------------
# OPTIMIZED AUDIO OUTPUT SWITCHER FOR HYPRLAND (MAKO OSD EDITION)
# Dependencies: hyprland, pulseaudio-utils (pactl), jq, libnotify (notify-send)
# -----------------------------------------------------------------------------
set -uo pipefail

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

# Dependency check
for cmd in pactl jq notify-send; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "Error: Required command '$cmd' not found." >&2
        exit 1
    fi
done

# 1. Get the current default sink name
current_sink=$(pactl get-default-sink 2>/dev/null || echo "")

# 2. THE LOGIC CORE - Single jq command with safety checks
#    Using NUL as delimiter to handle descriptions with tabs/newlines
sink_data=$(pactl -f json list sinks 2>/dev/null | jq -r --arg current "$current_sink" '
  # Filter: Keep sinks with no ports OR where at least one port is available
  [ .[] | select((.ports | length == 0) or ([.ports[]? | .availability != "not available"] | any)) ]
  | sort_by(.name) as $sinks
  | ($sinks | length) as $len

  # Safety: Exit early if no sinks available
  | if $len == 0 then ""
    else
      (($sinks | map(.name) | index($current)) // -1) as $idx
      | (if $idx < 0 then 0 else ($idx + 1) % $len end) as $next_idx
      | $sinks[$next_idx]
      | [
          .name,
          # Sanitize description: replace tabs/newlines with spaces
          ((.description // .properties."device.description" // .properties."node.description" // .properties."device.product.name" // .name) | gsub("[\\t\\n\\r]"; " ")),
          ((.volume | to_entries[0].value.value_percent // "0%") | sub("%$"; "")),
          (if .mute then "true" else "false" end)
        ]
      | @tsv
    end
')

# 3. Parse the output safely
IFS=$'\t' read -r next_name next_desc next_vol next_mute <<< "$sink_data"

# 4. Error handling: No sinks found or parsing failed
if [[ -z "${next_name:-}" ]]; then
    notify "audio-volume-muted-symbolic" "No Output Devices Available" ""
    exit 1
fi

# 5. Ensure volume is numeric (default to 0)
if ! [[ "${next_vol:-}" =~ ^[0-9]+$ ]]; then
    next_vol=0
fi

# 6. Switch the default sink
if ! pactl set-default-sink "$next_name" 2>/dev/null; then
    notify "dialog-error-symbolic" "Failed to switch output" ""
    exit 1
fi

# 7. Move all currently playing streams to the new sink
while IFS=$'\t' read -r input_id _; do
    [[ -n "$input_id" ]] && pactl move-sink-input "$input_id" "$next_name" 2>/dev/null || true
done < <(pactl list short sink-inputs 2>/dev/null)

# 8. Determine icon based on volume and mute status
if [[ "${next_mute:-}" == "true" ]] || (( next_vol == 0 )); then
    icon="audio-volume-muted-symbolic"
elif (( next_vol <= 33 )); then
    icon="audio-volume-low-symbolic"
elif (( next_vol <= 66 )); then
    icon="audio-volume-medium-symbolic"
else
    icon="audio-volume-high-symbolic"
fi

# 9. Display the OSD notification (passing volume for Mako bar support)
notify "$icon" "${next_desc:-Unknown Device}" "$next_vol"

exit 0
