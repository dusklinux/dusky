#!/usr/bin/env bash
# ==============================================================================
# DUSKY OUTPUT :: MODERN WAYLAND AUDIO SWITCHER
# Engine: WirePlumber (wpctl) / Native PipeWire
# ==============================================================================
set -euo pipefail

readonly ROFI_THEME_STR='window { width: 450px; } listview { lines: 6; }'
readonly SYNC_ID="sys-osd"

# 1. Native Dependency Check
for cmd in wpctl rofi notify-send awk; do
    if ! command -v "$cmd" &>/dev/null; then
        notify-send -u critical "Dusky Output" "Missing required dependency: $cmd"
        exit 1
    fi
done

declare -a rofi_options=()
declare -A device_map=()

# 2. Parse native WirePlumber status block for Sinks (Outputs)
#    This isolates the output block and guarantees we only parse valid sinks.
while IFS= read -r line; do
    # Extract Node ID
    [[ "$line" =~ ([0-9]+)\. ]] || continue
    id="${BASH_REMATCH[1]}"
    
    is_active=false
    [[ "$line" == *"*"* ]] && is_active=true
    
    is_muted=false
    [[ "$line" == *"MUTED"* ]] && is_muted=true
    
    # Extract clean name (Strip ID prefix and Volume suffix)
    name="${line#*${id}. }"
    name="${name% \[vol:*}"
    
    # Native Bash Whitespace Trimming
    name="${name#"${name%%[![:space:]]*}"}"
    name="${name%"${name##*[![:space:]]}"}"
    [[ -z "$name" ]] && continue
    
    # Assign Icons based on state
    if $is_muted; then
        icon=" "
    else
        icon=" "
    fi
    
    display_str="$icon  $name"
    if $is_active; then
        display_str+="  [Active]"
    fi
    
    # Handle identical hardware names gracefully
    unique_str="$display_str"
    count=2
    while [[ -n "${device_map[$unique_str]:-}" ]]; do
        unique_str="$display_str ($count)"
        ((count++))
    done
    
    rofi_options+=("$unique_str")
    device_map["$unique_str"]="$id"
    
done < <(wpctl status | awk '/Sinks:/{f=1; next} /Sources:|Filters:|Streams:|Settings:/{f=0} f')

if [[ ${#rofi_options[@]} -eq 0 ]]; then
    notify-send -u critical "Dusky Output" "No output devices found."
    exit 1
fi

# 3. Present UI
choice=$(printf '%s\n' "${rofi_options[@]}" | rofi -dmenu -i -p "󰓃  Select Output" -theme-str "$ROFI_THEME_STR" -format s)

# 4. Apply Execution & Trigger OSD
if [[ -n "$choice" && -n "${device_map[$choice]:-}" ]]; then
    target_id="${device_map[$choice]}"
    
    # WirePlumber handles the stream migration natively
    wpctl set-default "$target_id"
    
    # Extract friendly name for the notification
    clean_name="${choice/\[Active\]/}"
    clean_name="${clean_name/  /}"
    clean_name="${clean_name/  /}"
    clean_name=$(echo "$clean_name" | xargs)
    
    # Fetch accurate applied volume for the OSD payload
    vol_info=$(wpctl get-volume "$target_id")
    vol_val=$(echo "$vol_info" | awk '{print $2}')
    vol_pct=$(awk -v v="$vol_val" 'BEGIN { printf "%.0f", v * 100 }')
    
    osd_icon="audio-volume-high-symbolic"
    if [[ "$vol_info" == *"MUTED"* ]] || (( vol_pct == 0 )); then
        osd_icon="audio-volume-muted-symbolic"
    elif (( vol_pct <= 33 )); then
        osd_icon="audio-volume-low-symbolic"
    elif (( vol_pct <= 66 )); then
        osd_icon="audio-volume-medium-symbolic"
    fi
    
    notify-send -a "OSD" -h string:x-canonical-private-synchronous:"$SYNC_ID" -h int:value:"$vol_pct" -i "$osd_icon" "$clean_name"
fi
