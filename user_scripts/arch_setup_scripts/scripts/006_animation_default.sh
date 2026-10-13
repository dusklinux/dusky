#!/usr/bin/env bash
#d: Apply dusky's default Hyprland animations

set -euo pipefail

readonly CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}"
readonly SOURCE_FILE="$CONFIG_DIR/hypr/source/animations/dusky.lua"
script_path=$(realpath -e -- "${BASH_SOURCE[0]}")
readonly SWITCHER="${script_path%/*}/../../rofi/hypr_anim.sh"

if [[ ! -f "$SOURCE_FILE" ]]; then
    printf '[%(%T)T] [ERROR] Source missing: %s\n' -1 "$SOURCE_FILE" >&2
    exit 1
fi
if [[ ! -r "$SWITCHER" ]]; then
    printf '[%(%T)T] [ERROR] Animation switcher missing or unreadable: %s\n' -1 "$SWITCHER" >&2
    exit 1
fi

# Offline setup has no compositor session. Never guess another user's/session's instance.
args=()
if [[ -z "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]] || ! command -v hyprctl >/dev/null; then
    args+=(--no-reload)
fi

# Reuse the switcher's locking, atomic replacement, rollback, and saved-state updates.
# Ignore inherited Rofi selection metadata: this setup step always selects the default.
ROFI_INFO='' ROFI_RETV='' bash "$SWITCHER" "${args[@]}" horizontal "$SOURCE_FILE"
printf '[%(%T)T] [INFO] Switched animation to: dusky (horizontal)\n' -1
if ((${#args[@]})); then
    printf '[%(%T)T] [INFO] Hyprland reload skipped; saved for the next start.\n' -1
fi
