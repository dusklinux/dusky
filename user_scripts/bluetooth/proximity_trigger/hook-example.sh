#!/usr/bin/env bash
# Optional custom hook. Add this script's quoted path to the corresponding TOML
# command list to activate it. Default locking is configured separately in on_far.
# PROXIMITY_EVENT: far / near / connect / disconnect
# PROXIMITY_STATE: FAR / NEAR / UNKNOWN
# PROXIMITY_DEVICE_MAC, PROXIMITY_DEVICE_NAME, PROXIMITY_RSSI (dBm or N/A)
# Example:
# printf '%s: %s\n' "$PROXIMITY_EVENT" "$PROXIMITY_DEVICE_NAME"
