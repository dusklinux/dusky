#!/usr/bin/env bash
set -euo pipefail

for cmd in hyprctl jq; do
    command -v "$cmd" >/dev/null 2>&1 || { printf 'Error: %s not found\n' "$cmd" >&2; exit 1; }
done

readonly TARGET_CLASS='terminal_clipboard.sh'

mapfile -t addresses < <(hyprctl -j clients | jq -r --arg cls "$TARGET_CLASS" \
    '.[] | select((.class == $cls) or (.appId == $cls)) | .address')

if (( ${#addresses[@]} > 0 )); then
    batch_cmds=("${addresses[@]/#/dispatch closewindow address:}")

    (IFS=';'; hyprctl --batch "${batch_cmds[*]}") >/dev/null 2>&1 || true

    for _ in {1..20}; do
        sleep 0.05
        if ! hyprctl clients | grep -Fq "$TARGET_CLASS"; then
            break
        fi
    done
fi
exec "$@"
