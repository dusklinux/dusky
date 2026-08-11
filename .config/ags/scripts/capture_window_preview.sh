#!/usr/bin/env bash
set -u

ADDRESS="${1:-}"
DEST="${2:-}"

if [[ -z "$ADDRESS" || -z "$DEST" ]]; then
  echo "usage: capture_window_preview.sh ADDRESS DEST" >&2
  exit 1
fi

if ! command -v grim >/dev/null 2>&1 || ! command -v hyprctl >/dev/null 2>&1; then
  exit 1
fi

mkdir -p -- "$(dirname -- "$DEST")" || exit 1
TMP="$(mktemp --tmpdir="$(dirname -- "$DEST")" '.adaptive-glass-preview.XXXXXX.png')" || exit 1
trap 'rm -f -- "$TMP"' EXIT

RAW="${ADDRESS#0x}"
FULL="0x${RAW}"

capture() {
  rm -f -- "$TMP"
  if "$@" "$TMP" >/dev/null 2>&1 && [[ -s "$TMP" ]]; then
    mv -f -- "$TMP" "$DEST"
    trap - EXIT
    return 0
  fi
  return 1
}

# grim -T consumes ext-foreign-toplevel's stable identifier, not Hyprland's
# pointer-like window address. Hyprland exposes that identifier as stableId in
# `hyprctl -j clients`, so map the Astal client address before capturing.
CLIENTS_JSON="$(hyprctl -j clients 2>/dev/null)" || CLIENTS_JSON=""
STABLE_ID=""

if [[ -n "$CLIENTS_JSON" ]] && command -v jq >/dev/null 2>&1; then
  STABLE_ID="$(printf '%s' "$CLIENTS_JSON" | jq -r --arg address "$FULL" '.[] | select(.address == $address) | .stableId // empty' 2>/dev/null | head -n 1)"
elif [[ -n "$CLIENTS_JSON" ]] && command -v python3 >/dev/null 2>&1; then
  STABLE_ID="$(printf '%s' "$CLIENTS_JSON" | python3 -c 'import json,sys; a=sys.argv[1]; data=json.load(sys.stdin); print(next((str(x.get("stableId", "")) for x in data if x.get("address") == a), ""))' "$FULL" 2>/dev/null)"
fi

if [[ -n "$STABLE_ID" && "$STABLE_ID" != "null" ]]; then
  capture grim -T "$STABLE_ID" && exit 0
fi

# Legacy grim-hyprland builds accepted the Hyprland address directly with -w.
# Modern stock grim may reject this option; that is harmless and the caller
# will display the non-image fallback instead.
for identifier in "$FULL" "$RAW"; do
  capture grim -w "$identifier" && exit 0
done

exit 1
