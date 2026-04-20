#!/usr/bin/env bash
# Execution constraints for ultimate reliability and safety
set -euo pipefail

# Parse arguments
MODE="horizontal"
for arg in "$@"; do
    case "$arg" in
        --vertical) MODE="vertical" ;;
        --horizontal) MODE="horizontal" ;;
    esac
done

# Fetch buffers safely with DBus IPC timeout protection (prevents Waybar thread locks)
ACTIVE=$(timeout 1 makoctl list -j 2>/dev/null || true)
ACTIVE=${ACTIVE:-[]}

HISTORY=$(timeout 1 makoctl history -j 2>/dev/null || true)
HISTORY=${HISTORY:-[]}

# Fetch mode and evaluate DND purely in-memory
MAKO_MODE=$(timeout 1 makoctl mode 2>/dev/null || true)
DND_STATE=""
if [[ "$MAKO_MODE" =~ "do-not-disturb" ]]; then
    DND_STATE="true"
fi

# Define blacklist target and read securely
BLACKLIST_FILE="${XDG_RUNTIME_DIR:-/tmp}/mako_rofi_blacklist"
BLACKLIST_RAW=""
if [[ -r "$BLACKLIST_FILE" ]]; then
    BLACKLIST_RAW=$(<"$BLACKLIST_FILE" 2>/dev/null) || true
fi

# Unified O(1) JSON Processing & Native Serialization via jq
jq -c -n \
    --argjson active "$ACTIVE" \
    --argjson history "$HISTORY" \
    --arg bl "$BLACKLIST_RAW" \
    --arg dnd "$DND_STATE" \
    --arg mode "$MODE" '
    
    # Helper for vertical alignment centering
    def pad3:
        tostring |
        length as $l |
        if $l >= 3 then .
        elif $l == 2 then "\u2005" + . + "\u2005"
        elif $l == 1 then " " + . + " "
        else "   " end;

    "󰂛" as $dnd_icon | "󰂚" as $norm_icon |

    # 1. Construct O(1) lookup dictionary for blacklisted IDs
    ($bl | split("\n") | map(select(length > 0)) | reduce .[] as $id ({}; .[$id] = true)) as $blacklist_dict
    
    # 2. Calculate true pending count in a single filtered pass
    | ($active + $history) 
    | unique_by(.id) 
    | map(select(.summary != null and .summary != "" and .app_name != "OSD")) 
    | map(select($blacklist_dict[.id | tostring] | not))
    | length as $count
    
    # 3. Native JSON structural generation based on parsed arguments
    | if ($dnd != "") then
        {
            "text": (if $mode == "vertical" then ($count | pad3) + "\n" + ($dnd_icon | pad3) else (if $count == 0 then $dnd_icon else "\($dnd_icon) \($count)" end) end),
            "tooltip": "Do Not Disturb (\($count) pending)",
            "class": (if $count == 0 then "dnd" else "dnd-pending" end)
        }
      else
        {
            "text": (if $mode == "vertical" then ($count | pad3) + "\n" + ($norm_icon | pad3) else (if $count == 0 then "\($norm_icon) 0" else "\($norm_icon) \($count)" end) end),
            "tooltip": (if $count == 0 then "No notifications" else "\($count) pending notifications" end),
            "class": (if $count == 0 then "empty" else "pending" end)
        }
      end
'
