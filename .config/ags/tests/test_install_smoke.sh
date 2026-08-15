#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/home/.config/waybar" "$TMP/home/.config/ags" "$TMP/home/user_scripts/bar" "$TMP/home/user_scripts/hypr/defaults/edit_here" "$TMP/bin"
printf 'waybar-sentinel\n' > "$TMP/home/.config/waybar/KEEP_ME"
printf 'old-ags\n' > "$TMP/home/.config/ags/original.txt"
cat > "$TMP/bin/ags" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$TMP/bin/ags"
cat > "$TMP/home/user_scripts/bar/bar_switch.sh" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$TMP/home/user_scripts/bar/bar_switch.sh"
cat > "$TMP/home/user_scripts/hypr/defaults/edit_here/autostart.lua" <<'SH'
hl.on("hyprland.start", function()
    hl.exec_cmd("uwsm-app -- $HOME/user_scripts/bar/bar_switch.sh start")
end)
SH
HOME="$TMP/home" PATH="$TMP/bin:$PATH" "$ROOT/install.sh" --skip-deps --no-activate >/dev/null
test -f "$TMP/home/.config/waybar/KEEP_ME"
test -f "$TMP/home/.config/ags/.adaptive-glass-managed"
test -f "$TMP/home/.config/ags/app.tsx"
test -x "$TMP/home/.config/ags/scripts/capture_window_preview.sh"
BACKUP="$(find "$TMP/home/.config" -maxdepth 1 -type d -name 'ags.backup-*' -print -quit)"
test -n "$BACKUP"
test -f "$BACKUP/original.txt"
echo "install smoke: PASS"
