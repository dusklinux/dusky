#!/bin/bash

RADIO_CMD="$HOME/user_scripts/audio/radio/dusky-cmd-radio"

# Fetch stations
STATIONS=$("$RADIO_CMD" stations)

# Build menu options
MENU=" Stop Radio\n"
MENU+=" Resume Radio\n"

# Parse JSON into lines: Label [id]
while read -r line; do
  if [[ -n "$line" ]]; then
    label=$(echo "$line" | jq -r '.label')
    id=$(echo "$line" | jq -r '.id')
    MENU+=" $label [$id]\n"
  fi
done <<< "$STATIONS"

# Show rofi menu
CHOICE=$(echo -e "$MENU" | rofi -dmenu -i -p "Live Radio")

if [[ -z "$CHOICE" ]]; then
  exit 0
fi

if [[ "$CHOICE" == *"Stop Radio"* ]]; then
  "$RADIO_CMD" stop
elif [[ "$CHOICE" == *"Resume Radio"* ]]; then
  "$RADIO_CMD" resume
else
  # Extract ID inside brackets
  STATION_ID=$(echo "$CHOICE" | grep -o '\[.*\]' | sed 's/\[//;s/\]//')
  if [[ -n "$STATION_ID" ]]; then
    "$RADIO_CMD" start "$STATION_ID"
  fi
fi
