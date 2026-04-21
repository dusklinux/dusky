#!/usr/bin/env bash

# Fetch active and history buffers
ACTIVE=$(makoctl list -j 2>/dev/null || echo "[]")
HISTORY=$(makoctl history -j 2>/dev/null || echo "[]")

[[ -z "$ACTIVE" ]] && ACTIVE="[]"
[[ -z "$HISTORY" ]] && HISTORY="[]"

BLACKLIST_FILE="${XDG_RUNTIME_DIR:-/tmp}/mako_rofi_blacklist"
BLACKLIST_RAW=$(cat "$BLACKLIST_FILE" 2>/dev/null || echo "")

# 1. Parse JSON: Tag sources, sanitize Pango, and format for Rofi
MENU_PAYLOAD=$(jq -r -n \
  --argjson active "$ACTIVE" \
  --argjson history "$HISTORY" \
  --arg bl "$BLACKLIST_RAW" '
  
  def escape_pango: 
      if type == "string" then 
        gsub("&"; "&amp;") | gsub("<"; "&lt;") | gsub(">"; "&gt;") | gsub("\""; "&quot;") | gsub("'\''"; "&apos;")
      else 
        . 
      end;

  def clean_app: 
      if .app_name == "notify-send" or .app_name == "mako" or .app_name == null then 
        "" 
      else 
        "[\(.app_name | escape_pango)] " 
      end;
      
  def clean_body:
      if .body == null or .body == "" then
        "\n<span alpha=\"50%\" size=\"smaller\"><i>No additional details</i></span>"
      else
        "\n<span alpha=\"75%\" size=\"smaller\">\((.body) | gsub("<[^>]+>"; "") | escape_pango | gsub("\n"; " ") | sub("^\\s+"; ""))</span>"
      end;

  # Parse blacklist into an array
  ($bl | split("\n") | map(select(. != ""))) as $blacklisted_ids

  # Tag objects with their origin buffer so we know if DBus URLs are still alive
  | ($active | map(. + {__source: "active"})) as $a
  | ($history | map(. + {__source: "history"})) as $h

  # Combine, deduplicate (active wins), sort chronologically
  | ($a + $h) 
  | unique_by(.id) 
  | sort_by(.id) 
  | reverse 
  | .[] 
  | select(.summary != null and .summary != "") 
  | select(.app_name != "OSD")
  
  # Filter out blacklisted IDs
  | select((.id | tostring) as $id_str | $blacklisted_ids | index($id_str) | not)
  
  # Output Format: ID [TAB] APP [TAB] SOURCE [TAB] UI_STRING [RECORD_SEPARATOR]
  | "\(.id)\t\(.desktop_entry // .app_name // "" | gsub("\t";" "))\t\(.__source)\t<b>\(clean_app)\(.summary | gsub("<[^>]+>"; "") | escape_pango)</b>\(clean_body)\u001e"
')

if [[ -z "$MENU_PAYLOAD" ]]; then
    notify-send -t 1500 "󰎟 Notifications" "No notifications in buffer."
    exit 0
fi

# 2. Safely parse the payload into parallel arrays
ID_ARRAY=()
APP_ARRAY=()
SRC_ARRAY=()
MENU_STRING=""

while IFS=$'\t' read -r -d $'\x1e' id app source text; do
    [[ -z "$id" ]] && continue
    ID_ARRAY+=("$id")
    APP_ARRAY+=("$app")
    SRC_ARRAY+=("$source")
    MENU_STRING+="${text}"$'\x1e'
done <<< "$MENU_PAYLOAD"

# 3. Execute Rofi
SELECTED_INDEX=$(echo -n "$MENU_STRING" | rofi -dmenu -i -p "󰎟 Notifications" \
    -mesg "<b>Alt+y</b>: Clear All  |  <b>Enter/Click</b>: Action &amp; Dismiss" \
    -markup-rows \
    -sep '\x1e' \
    -format 'i' \
    -eh 2 \
    -kb-custom-2 "Alt+y" \
    -hover-select \
    -me-select-entry '' \
    -me-accept-entry 'MousePrimary' \
    -theme-str 'window {width: 45%;} listview {lines: 6; fixed-height: false;} element {padding: 10px 14px;} element-text {vertical-align: 0.5;}')

ROFI_EXIT=$?

# 4. Handle Execution safely and asynchronously
case $ROFI_EXIT in
    0)
        # Verify SELECTED_INDEX is a valid positive integer
        if [[ "$SELECTED_INDEX" =~ ^[0-9]+$ ]]; then
            SELECTED_ID="${ID_ARRAY[$SELECTED_INDEX]}"
            SELECTED_APP="${APP_ARRAY[$SELECTED_INDEX]}"
            SELECTED_SRC="${SRC_ARRAY[$SELECTED_INDEX]}"
            
            if [[ "$SELECTED_SRC" == "active" ]]; then
                # 1. Trigger DBus action for live notifications (handles URLs flawlessly)
                makoctl invoke -n "$SELECTED_ID" default 2>/dev/null
            else
                # 2. Fallback for expired history items (Brings app to front safely)
                if [[ -n "$SELECTED_APP" && "$SELECTED_APP" != "notify-send" && "$SELECTED_APP" != "mako" ]]; then
                    # Grouped and backgrounded cleanly to prevent Rofi/Hyprland hanging
                    { gtk-launch "$SELECTED_APP" || hyprctl dispatch exec "$SELECTED_APP"; } >/dev/null 2>&1 &
                fi
            fi
            
            # 3. Dismiss from screen and Blacklist from UI
            makoctl dismiss -n "$SELECTED_ID" 2>/dev/null
            echo "$SELECTED_ID" >> "$BLACKLIST_FILE"
        fi
        ;;
    11)
        # Alt+Y Wipe
        rm -f "$BLACKLIST_FILE"
        
        if systemctl --user is-active --quiet mako.service; then
            systemctl --user restart mako.service
        else
            pkill -x mako && uwsm app -- mako &
        fi
        ;;
esac
