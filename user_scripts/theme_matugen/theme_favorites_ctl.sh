#!/usr/bin/env bash
set -euo pipefail
# ---------------- CONFIG ----------------

WALL_DIR="$HOME/Pictures/wallpapers/active_theme"
STATE_DIR="$HOME/.config/dusky/settings/dusky_theme"
FAV_FILE="$STATE_DIR/favorites.list"
CACHE_FILE="$STATE_DIR/current_wallpaper.cache"
INDEX_FILE="$STATE_DIR/favorites.index"

THEME_CTL="dusky-theme-ctl"

mkdir -p "$STATE_DIR"
touch "$FAV_FILE" "$CACHE_FILE" "$INDEX_FILE"

# ---------------- DEPENDENCY CHECK ----------------

if ! command -v awww >/dev/null 2>&1; then
  notify-send "Favorites" "awww not installed"
  exit 1
fi

if ! command -v notify-send >/dev/null 2>&1; then
  echo "notify-send missing"
  exit 1
fi

# ---------------- UTIL ----------------

normalize() {
  echo "$1" | tr -d '\r' | xargs
}

get_current_wallpaper() {

  local img=""

  img=$(awww query 2>/dev/null | sed -n 's/.*image: //p')

  if [[ -n "$img" && -f "$img" ]]; then
    echo "$img" >"$CACHE_FILE"
    echo "$img"
    return
  fi

  if [[ -s "$CACHE_FILE" ]]; then
    cat "$CACHE_FILE"
    return
  fi

  echo ""
}

# silent flag supported
apply_wallpaper() {

  local img="$1"
  local silent="${2:-0}"

  [[ -f "$img" ]] || {
    notify-send "Favorites" "Wallpaper missing: $(basename "$img")"
    exit 1
  }

  echo "$img" >"$CACHE_FILE"

  if [[ -x "$THEME_CTL" ]]; then
    "$THEME_CTL" set "$img" 2>/dev/null || awww img "$img"
  else
    awww img "$img"
  fi

  if [[ "$silent" != "1" ]]; then
    notify-send "Favorites" "Applied: $(basename "$img")"
  fi
}

# ---------------- FAVORITE STATUS ----------------

find_position() {

  local target="$1"
  mapfile -t favs <"$FAV_FILE"

  for i in "${!favs[@]}"; do
    if [[ "${favs[$i]}" == "$target" ]]; then
      echo "$((i + 1)) ${#favs[@]}"
      return
    fi
  done

  echo "0 ${#favs[@]}"
}

# ---------------- ADD ----------------

add_favorite() {

  local name="$1"

  read -r pos total <<<"$(find_position "$name")"

  if ((pos > 0)); then

    notify-send "Favorites" \
      "Already favorite: $name\nPosition: $pos / $total"

    return
  fi

  echo "$name" >>"$FAV_FILE"
  sort -u "$FAV_FILE" -o "$FAV_FILE"

  read -r pos total <<<"$(find_position "$name")"

  notify-send "Favorites" \
    "Added favorite: $name\nPosition: $pos / $total"
}

# ---------------- REMOVE ----------------

remove_favorite() {

  local name="$1"

  read -r pos total <<<"$(find_position "$name")"

  if ((pos == 0)); then

    notify-send "Favorites" \
      "Not in favorites: $name"

    return
  fi

  grep -Fxv "$name" "$FAV_FILE" >"$FAV_FILE.tmp"
  mv "$FAV_FILE.tmp" "$FAV_FILE"

  notify-send "Favorites" \
    "Removed favorite: $name"
}

# ---------------- TOGGLE ----------------

toggle_favorite() {

  local current
  current=$(get_current_wallpaper)

  [[ -z "$current" ]] && {
    notify-send "Favorites" "No wallpaper detected"
    exit 1
  }

  local name
  name=$(normalize "$(basename "$current")")

  add_favorite "$name"
}

# ---------------- DELETE CURRENT ----------------

delete_current() {

  local current
  current=$(get_current_wallpaper)

  [[ -z "$current" ]] && {
    notify-send "Favorites" "No wallpaper detected"
    exit 1
  }

  local name
  name=$(normalize "$(basename "$current")")

  remove_favorite "$name"
}

# ---------------- CYCLE ----------------

cycle_favorite() {

  mapfile -t favs <"$FAV_FILE"
  local total=${#favs[@]}

  if ((total == 0)); then
    notify-send "Favorites" "No favorites saved"
    exit 0
  fi

  local index=0

  if [[ -s "$INDEX_FILE" ]]; then
    index=$(cat "$INDEX_FILE" 2>/dev/null || echo 0)
    [[ "$index" =~ ^[0-9]+$ ]] || index=0
  fi

  index=$(((index + 1) % total))
  echo "$index" >"$INDEX_FILE"

  local next="${favs[$index]}"
  local full="$WALL_DIR/$next"

  [[ -f "$full" ]] || {
    notify-send "Favorites" "Missing file: $next"
    exit 1
  }

  apply_wallpaper "$full" 1

  notify-send "Favorites" \
    "Favorite: $next\nPosition: $((index + 1)) / $total"
}

# ---------------- LIST ----------------

list_favorites() {

  notify-send "Favorites location" "$FAV_FILE"
  cat "$FAV_FILE"
}

# ---------------- ENTRY ----------------

case "${1:-}" in

toggle)
  toggle_favorite
  ;;

remove)
  delete_current
  ;;

cycle)
  cycle_favorite
  ;;

list)
  list_favorites
  ;;

*)
  echo "Usage:"
  echo "  toggle  - add current wallpaper"
  echo "  remove  - remove current wallpaper"
  echo "  cycle   - cycle favorites"
  echo "  list    - show favorites"
  ;;
esac

# bindd = $mainMod, H, Add Favorite, exec, uwsm-app -- $user_scripts/theme_matugen/theme_favorites_ctl.sh toggle
#
# bindd = $mainMod SHIFT, apostrophe, Cycle Favorite, exec, uwsm-app -- $user_scripts/theme_matugen/theme_favorites_ctl.sh cycle
#
# bindd = $mainMod SHIFT, D, Remove Favorite, exec, uwsm-app -- $user_scripts/theme_matugen/theme_favorites_ctl.sh remove
#
# bind = SUPER SHIFT, W, exec, $user_scripts/rofi/rofi_wallpaper_selctor.sh fav
