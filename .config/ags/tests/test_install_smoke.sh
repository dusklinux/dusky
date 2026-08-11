#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/home/.config/waybar" "$TMP/home/.config/ags" "$TMP/bin"
printf 'waybar-sentinel\n' > "$TMP/home/.config/waybar/KEEP_ME"
printf 'old-ags\n' > "$TMP/home/.config/ags/original.txt"
cat > "$TMP/bin/ags" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$TMP/bin/ags"
HOME="$TMP/home" PATH="$TMP/bin:$PATH" "$ROOT/install.sh" >/dev/null
test -f "$TMP/home/.config/waybar/KEEP_ME"
test -f "$TMP/home/.config/ags/.adaptive-glass-managed"
test -f "$TMP/home/.config/ags/app.tsx"
test -x "$TMP/home/.config/ags/scripts/capture_window_preview.sh"
BACKUP="$(find "$TMP/home/.config" -maxdepth 1 -type d -name 'ags.backup-*' -print -quit)"
test -n "$BACKUP"
test -f "$BACKUP/original.txt"
echo "install smoke: PASS"
