#!/usr/bin/env bash
# Restart only this local test build, then open it on demand.
set -euo pipefail
readonly PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly BIN="$PROJECT_DIR/bin/dusky-tray"
if [[ ! -x "$BIN" ]]; then
    printf 'Missing %s. Run scripts/build-on-tmpfs.sh first.\n' "$BIN" >&2
    exit 1
fi
# Check executable paths so an installed build or another user's panel is untouched.
while IFS= read -r pid; do
    executable="$(readlink -- "/proc/$pid/exe" 2>/dev/null || true)"
    [[ "$executable" == "$BIN" || "$executable" == "$BIN (deleted)" ]] || continue
    kill -TERM -- "$pid" 2>/dev/null || true
    for ((attempt = 0; attempt < 20; attempt++)); do
        kill -0 -- "$pid" 2>/dev/null || break
        sleep 0.05
    done
    executable="$(readlink -- "/proc/$pid/exe" 2>/dev/null || true)"
    if [[ "$executable" == "$BIN" || "$executable" == "$BIN (deleted)" ]]; then
        kill -KILL -- "$pid" 2>/dev/null || true
    fi
done < <(pgrep -u "$UID" -f '(^|/)dusky-tray([[:space:]]|$)' || true)
exec "$BIN" "$@"
