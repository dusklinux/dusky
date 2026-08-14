#!/usr/bin/env bash
set -euo pipefail

SRC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.config/ags"
STAMP="$(date +%Y%m%d-%H%M%S)"
SRC_REAL="$(cd "$SRC_DIR" && pwd -P)"

if ! command -v ags >/dev/null 2>&1; then
  echo "ERROR: ags is not installed. Run the prerequisite installer first." >&2
  exit 1
fi

mkdir -p "$HOME/.config"

DEST_REAL=""
if [[ -d "$DEST" ]]; then
  DEST_REAL="$(cd "$DEST" && pwd -P)"
fi

IN_PLACE=false
if [[ "$DEST_REAL" == "$SRC_REAL" ]]; then
  IN_PLACE=true
fi

if [[ "$IN_PLACE" == false && -e "$DEST" && ! -f "$DEST/.adaptive-glass-managed" ]]; then
  BACKUP="$HOME/.config/ags.backup-$STAMP"
  echo "Existing AGS configuration detected; moving it to: $BACKUP"
  mv "$DEST" "$BACKUP"
fi

if [[ "$IN_PLACE" == false ]]; then
  mkdir -p "$DEST"
  find "$DEST" -mindepth 1 -maxdepth 1 ! -name '.adaptive-glass-managed' -exec rm -rf {} +
  cp -a "$SRC_DIR/app.tsx" "$SRC_DIR/style.css" "$SRC_DIR/components" "$SRC_DIR/lib" "$SRC_DIR/styles" "$SRC_DIR/scripts" "$DEST/"
fi

touch "$DEST/.adaptive-glass-managed"
chmod +x "$DEST/app.tsx"
chmod +x "$DEST/scripts/"*.sh

printf '\nGenerating AGS/GI type definitions...\n'
ags types -u -d "$DEST" || echo "WARN: type generation failed; AGS can still attempt to run the config."

cat <<MSG

Adaptive Glass AGS v2.10 installed at:
  $DEST

Waybar was not changed.

Run the shell manually with either:
  ags run "$HOME/.config/ags/app.tsx"
  "$HOME/.config/ags/app.tsx"

If an AGS instance is already running, stop that AGS instance before testing this one.
MSG
