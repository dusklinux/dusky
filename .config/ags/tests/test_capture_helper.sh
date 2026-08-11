#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/out"

cat > "$TMP/bin/hyprctl" <<'SH'
#!/usr/bin/env bash
if [[ "$*" == "-j clients" ]]; then
cat <<'JSON'
[{"address":"0xabc123","stableId":"18000017"}]
JSON
exit 0
fi
exit 1
SH

cat > "$TMP/bin/grim" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CAPTURE_LOG"
last="${!#}"
if [[ "$1" == "-T" && "$2" == "18000017" ]]; then
  printf 'fake-png' > "$last"
  exit 0
fi
exit 1
SH
chmod +x "$TMP/bin/hyprctl" "$TMP/bin/grim"

export CAPTURE_LOG="$TMP/grim.log"
PATH="$TMP/bin:$PATH" "$ROOT/scripts/capture_window_preview.sh" abc123 "$TMP/out/preview.png"
test -s "$TMP/out/preview.png"
grep -q -- '-T 18000017' "$TMP/grim.log"
echo "capture helper: PASS"
