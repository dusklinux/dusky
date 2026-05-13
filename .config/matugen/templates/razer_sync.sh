#!/usr/bin/env bash
# Sync active theme color to Razer keyboard via Polychromatic.
# Set RAZER_DEVICE to match your device name (polychromatic-cli --list-devices).
# Skips silently if polychromatic-cli is not installed or no device is found.

RAZER_DEVICE="${RAZER_DEVICE:-Razer Huntsman Mini}"
command -v polychromatic-cli >/dev/null 2>&1 || exit 0

# Stealing the light theme's primary color for maximum physical LED saturation and brightness
polychromatic-cli -n "$RAZER_DEVICE" -o static -c "{{colors.primary.light.hex}}" || :
