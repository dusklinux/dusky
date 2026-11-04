#!/usr/bin/env bash
# Restart the installed Dusky Tray, then open it on demand.
set -euo pipefail
readonly BIN="$HOME/.local/bin/dusky-tray"
if [[ ! -x "$BIN" ]]; then
    printf 'Missing %s. Run 152_dusky_tray_setup.py first.\n' "$BIN" >&2
    exit 1
fi
readonly EXECUTABLE="$(readlink -f -- "$BIN")"
# Match the installed executable, including its old inode after an atomic update.
while IFS= read -r pid; do
    executable="$(readlink -- "/proc/$pid/exe" 2>/dev/null || true)"
    [[ "$executable" == "$EXECUTABLE" || "$executable" == "$EXECUTABLE (deleted)" ]] || continue
    kill -TERM -- "$pid" 2>/dev/null || true
    for ((attempt = 0; attempt < 20; attempt++)); do
        kill -0 -- "$pid" 2>/dev/null || break
        sleep 0.05
    done
    executable="$(readlink -- "/proc/$pid/exe" 2>/dev/null || true)"
    if [[ "$executable" == "$EXECUTABLE" || "$executable" == "$EXECUTABLE (deleted)" ]]; then
        kill -KILL -- "$pid" 2>/dev/null || true
    fi
done < <(pgrep -u "$UID" -f '(^|/)dusky-tray([[:space:]]|$)' || true)
exec "$BIN" "$@"
