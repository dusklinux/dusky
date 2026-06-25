#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Rofi Wallpaper Selector (Matugen V4 Aligned & UWSM Patched)
# Target: Arch Linux / Hyprland / Dusky / UWSM
# -----------------------------------------------------------------------------
# Updates:
# - Synchronized with theme_ctl.sh state machine.
# - Dynamically inherits Awww transitions from state.conf.
# - Cleaned UI: Pango markup injected into Rofi to eliminate "crammed" text.
# - Added Alt+H binding to Apply WITHOUT Matugen color regeneration.
# - FIXED: awww query iteration logic now properly identifies active wallpaper.
# - FIXED: Universal scaling logic mapped dynamically to Hyprland dimensions.
# - FIXED: Eliminated empty bottom gaps with strict shrink-wrap directives.
# - REFACTORED: Instituted algorithmically immutable 8-item spatial geometry.
# -----------------------------------------------------------------------------

set -Eeuo pipefail

readonly APP_NAME="Wallpaper Menu"
readonly SCRIPT_NAME="${0##*/}"

readonly LOCK_FILE="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/rofi-wallpaper-selector.lock"

readonly WALLPAPER_DIR="$HOME/Pictures/wallpapers"
readonly SETTINGS_DIR="$HOME/.config/dusky/settings"
readonly FAVORITES_FILE="$SETTINGS_DIR/dusky_theme/wal_fav_rofi"
readonly STATE_FILE="$SETTINGS_DIR/dusky_theme/state.conf"
readonly FAV_STATE_FILE="$SETTINGS_DIR/dusky_theme/current_fav"
readonly THEME_CTL="${HOME}/user_scripts/theme_matugen/theme_ctl.sh"

readonly THUMB_SIZE=300
readonly CACHE_VERSION=4
readonly CACHE_DIR="$HOME/.cache/rofi-wallpaper-thumbs/v${CACHE_VERSION}-${THUMB_SIZE}"
readonly THUMB_DIR="$CACHE_DIR/thumbs"
readonly CACHE_FILE="$CACHE_DIR/rofi_input.cache"
readonly FAVORITES_CACHE_FILE="$CACHE_DIR/rofi_input_fav.cache"
readonly SOURCE_STATE_FILE="$CACHE_DIR/source.state"
readonly PLACEHOLDER="$CACHE_DIR/_placeholder.png"

readonly ROFI_THEME="$HOME/.config/rofi/wallpaper.rasi"
readonly LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/rofi-wallpaper-selector"
readonly LOG_FILE="$LOG_DIR/wallpaper-selector.log"

declare -ag TEMP_FILES=()
declare -gi SHOW_FAVORITES=0
declare -gi CYCLE_FAV=0
declare -gi FORCE_REBUILD=0
declare -gi SHOW_PROGRESS=0
declare -gi CACHE_ONLY=0
declare -gi PROGRESS_ACTIVE=0
declare -gi MAX_JOBS=1

# --- DEFAULT ANIMATION STATE ---
CUR_T_TYPE="random"
CUR_T_DUR="2"
CUR_T_FPS="60"
CUR_T_BEZ=".54,0,.34,.99"
CUR_T_ANG="30"
CUR_T_POS="center"

log() {
  local level=$1
  shift

  local ts line
  local progress_active=${PROGRESS_ACTIVE:-0}

  printf -v ts '%(%F %T)T' -1
  printf -v line '[%s] [%s] [pid=%s] %s' "$ts" "$level" "$BASHPID" "$*"

  if ((progress_active == 0)); then
    printf '%s\n' "$line" >&2
  fi
  { printf '%s\n' "$line" >>"$LOG_FILE"; } 2>/dev/null || true
}

log_output() {
  local level=$1
  local prefix=$2
  local text=${3-}
  local line

  if [[ -z $text ]]; then
    log "$level" "$prefix"
    return 0
  fi

  while IFS= read -r line || [[ -n $line ]]; do
    log "$level" "${prefix}${line}"
  done <<<"$text"
}

notify() {
  local summary=$1
  local body=${2-}
  local urgency=${3:-low}
  local timeout=${4:-1500}

  if command -v notify-send >/dev/null 2>&1; then
    if [[ -n $body ]]; then
      notify-send -a "$APP_NAME" "$summary" "$body" -u "$urgency" -t "$timeout" >/dev/null 2>&1 || true
    else
      notify-send -a "$APP_NAME" "$summary" -u "$urgency" -t "$timeout" >/dev/null 2>&1 || true
    fi
  else
    if [[ -n $body ]]; then
      printf '%s: %s\n' "$summary" "$body" >&2
    else
      printf '%s\n' "$summary" >&2
    fi
  fi
}

die() {
  local summary=$1
  local body=${2-}
  local notify_body

  if [[ -n $body ]]; then
    log_output ERROR "${summary}: " "$body"
    notify_body="${body}"$'\n'"Log: $LOG_FILE"
  else
    log ERROR "$summary"
    notify_body="Log: $LOG_FILE"
  fi

  notify "$summary" "$notify_body" critical 5000
  exit 1
}

register_temp() {
  TEMP_FILES+=("$1")
}

cleanup_temps() {
  local tmp
  for tmp in "${TEMP_FILES[@]}"; do
    [[ -n $tmp ]] && rm -f -- "$tmp"
  done
}

on_err() {
  local rc=$?
  set +e
  log ERROR "Unhandled error at line ${BASH_LINENO[0]} while running: ${BASH_COMMAND} (exit ${rc})"
  exit "$rc"
}

trap cleanup_temps EXIT

setup_logging() {
  mkdir -p -- "$LOG_DIR" 2>/dev/null || true
  touch -- "$LOG_FILE" 2>/dev/null || true
  log INFO "===== ${SCRIPT_NAME} start ====="
  log INFO "Log file: $LOG_FILE"
}

acquire_lock() {
  exec 200>"$LOCK_FILE"
  if ! flock -n 200; then
    log INFO "Another instance is already running."
    notify "Wallpaper selector already running."
    exit 0
  fi
}

release_lock() {
  exec 200>&- 2>/dev/null || true
}

parse_args() {
  while (($#)); do
    case $1 in
      fav|favorites|--favorites)
        SHOW_FAVORITES=1
        ;;
      --next-fav|next-fav)
        CYCLE_FAV=1
        ;;
      --rebuild-cache|rebuild-cache|--regenerate|regenerate)
        FORCE_REBUILD=1
        ;;
      --progress|-p)
        SHOW_PROGRESS=1
        ;;
      --cache-only|cache-only|--no-menu|no-menu)
        CACHE_ONLY=1
        ;;
      -h|--help)
        printf 'Usage: %s [fav|--next-fav] [--rebuild-cache] [--progress|-p] [--cache-only]\n' "$SCRIPT_NAME"
        exit 0
        ;;
      *)
        die "Unknown argument." "$1"
        ;;
    esac
    shift
  done
}

check_dependencies() {
  local -a missing=()
  local cmd

  for cmd in rofi awww magick matugen uwsm-app setsid flock sha256sum find sort xargs cmp stat nproc gawk mktemp jq; do
    command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
  done

  if ((${#missing[@]})); then
    local joined
    printf -v joined '%s, ' "${missing[@]}"
    die "Missing dependency." "${joined%, }"
  fi
}

initialize_runtime() {
  MAX_JOBS=$(nproc)
  ((MAX_JOBS > 0)) || MAX_JOBS=1
  log INFO "Parallel thumbnail jobs: $MAX_JOBS"
}

validate_config() {
  [[ -d "$WALLPAPER_DIR" ]] || die "Wallpaper directory not found." "$WALLPAPER_DIR"
  [[ -r "$WALLPAPER_DIR" ]] || die "Wallpaper directory is not readable." "$WALLPAPER_DIR"
  [[ -x "$WALLPAPER_DIR" ]] || die "Wallpaper directory is not searchable." "$WALLPAPER_DIR"

  mkdir -p -- \
    "$CACHE_DIR" \
    "$THUMB_DIR" \
    "$SETTINGS_DIR" \
    "$(dirname -- "$LOCK_FILE")" \
    "$LOG_DIR" \
    "${SETTINGS_DIR}/dusky_theme"
}

ensure_placeholder() {
  [[ -f "$PLACEHOLDER" ]] && return 0

  local tmp output
  tmp=$(mktemp --tmpdir="$CACHE_DIR" --suffix=.png placeholder.tmp.XXXXXX)
  register_temp "$tmp"

  if ! output=$(magick \
    -size "${THUMB_SIZE}x${THUMB_SIZE}" \
    xc:"#333333" \
    "$tmp" 2>&1); then
    die "Failed to create placeholder thumbnail." "$output"
  fi

  mv -f -- "$tmp" "$PLACEHOLDER"
  log INFO "Created placeholder thumbnail."
}

is_supported_rel_path() {
  local rel=$1

  [[ -n $rel ]] || return 1
  [[ $rel != /* ]] || return 1
  [[ $rel != *$'\n'* ]] || return 1
  [[ $rel != *$'\r'* ]] || return 1
  [[ $rel != *$'\t'* ]] || return 1
  [[ $rel != *$'\x1f'* ]] || return 1
  [[ ! $rel =~ (^|/)\.\.(/|$) ]] || return 1
}

find_wallpapers() {
  find "$WALLPAPER_DIR" -type f \
    \( -iname '*.jpg' \
    -o -iname '*.jpeg' \
    -o -iname '*.png' \
    -o -iname '*.webp' \
    -o -iname '*.gif' \) \
    "$@"
}

scan_candidate_files() {
  local out_file=$1
  if ! find_wallpapers -print0 | LC_ALL=C sort -z >"$out_file"; then
    return 1
  fi
}

write_source_state() {
  local out_file=$1
  if ! find_wallpapers -printf '%P\t%s\t%T@\t%i\0' | LC_ALL=C sort -z >"$out_file"; then
    return 1
  fi
}

cache_is_current() {
  [[ -f "$CACHE_FILE" ]] || return 1
  [[ -f "$SOURCE_STATE_FILE" ]] || return 1

  local state_tmp
  state_tmp=$(mktemp --tmpdir="$CACHE_DIR" source.state.check.tmp.XXXXXX)
  register_temp "$state_tmp"

  if ! write_source_state "$state_tmp"; then
    log ERROR "Failed to build source state for cache validation."
    return 1
  fi

  cmp -s -- "$SOURCE_STATE_FILE" "$state_tmp"
}

thumb_path() {
  local rel=$1
  local digest
  digest=$(printf '%s' "$rel" | sha256sum)
  printf '%s/%s.png\n' "$THUMB_DIR" "${digest%% *}"
}

generate_thumb() {
  local file=$1
  local rel thumb tmp output

  rel=${file#"$WALLPAPER_DIR"/}
  is_supported_rel_path "$rel" || return 0

  thumb=$(thumb_path "$rel")

  if [[ -f "$thumb" && "$thumb" -nt "$file" ]]; then
    return 0
  fi

  tmp=$(mktemp --tmpdir="$THUMB_DIR" --suffix=.png thumb.tmp.XXXXXX) || return 1

  if output=$(nice -n 19 magick \
    -limit thread 1 \
    "$file" \
    -auto-orient \
    -strip \
    -thumbnail "${THUMB_SIZE}x${THUMB_SIZE}^" \
    -gravity center \
    -extent "${THUMB_SIZE}x${THUMB_SIZE}" \
    "$tmp" 2>&1); then
    mv -f -- "$tmp" "$thumb"
    return 0
  fi

  rm -f -- "$tmp"
  log_output WARN "Thumbnail generation failed for '$rel': " "$output"
  return 1
}

generate_thumb_safe() {
  generate_thumb "$1" || true
}

export WALLPAPER_DIR THUMB_DIR THUMB_SIZE LOG_FILE PROGRESS_ACTIVE
export -f log log_output is_supported_rel_path thumb_path generate_thumb generate_thumb_safe

cleanup_orphan_thumbs() {
  local -n rels_ref=$1
  local -A keep=()
  local rel thumb existing_thumb

  for rel in "${rels_ref[@]}"; do
    thumb=$(thumb_path "$rel")
    keep["$thumb"]=1
  done

  while IFS= read -r -d '' existing_thumb; do
    [[ ${keep[$existing_thumb]+_} ]] && continue
    rm -f -- "$existing_thumb"
  done < <(find "$THUMB_DIR" -maxdepth 1 -type f -name '*.png' -print0)
}

build_cache() {
  log INFO "Building wallpaper cache."
  notify "Building Cache" "Generating thumbnails for wallpapers..."

  local scan_tmp state_tmp cache_tmp
  local -a files=()
  local -a valid_files=()
  local -a valid_rels=()
  local file rel thumb
  local -i skipped=0
  local -i render_progress=0

  scan_tmp=$(mktemp --tmpdir="$CACHE_DIR" scan.tmp.XXXXXX)
  state_tmp=$(mktemp --tmpdir="$CACHE_DIR" source.state.tmp.XXXXXX)
  cache_tmp=$(mktemp --tmpdir="$CACHE_DIR" rofi_input.cache.tmp.XXXXXX)
  register_temp "$scan_tmp"
  register_temp "$state_tmp"
  register_temp "$cache_tmp"

  if ! scan_candidate_files "$scan_tmp"; then
    die "Failed to scan wallpaper directory." "$WALLPAPER_DIR"
  fi

  if ! write_source_state "$state_tmp"; then
    die "Failed to build wallpaper source state." "$WALLPAPER_DIR"
  fi

  mapfile -d '' files <"$scan_tmp"

  if ((${#files[@]} == 0)); then
    : >"$cache_tmp"
    mv -f -- "$cache_tmp" "$CACHE_FILE"
    mv -f -- "$state_tmp" "$SOURCE_STATE_FILE"
    rm -f -- "$FAVORITES_CACHE_FILE"
    find "$THUMB_DIR" -maxdepth 1 -type f -name '*.png' -delete
    notify "No wallpapers found." "$WALLPAPER_DIR"
    log INFO "No wallpapers found."
    return 1
  fi

  for file in "${files[@]}"; do
    rel=${file#"$WALLPAPER_DIR"/}

    if ! is_supported_rel_path "$rel"; then
      ((skipped += 1))
      continue
    fi

    valid_files+=("$file")
    valid_rels+=("$rel")
  done

  if ((${#valid_files[@]} == 0)); then
    : >"$cache_tmp"
    mv -f -- "$cache_tmp" "$CACHE_FILE"
    mv -f -- "$state_tmp" "$SOURCE_STATE_FILE"
    rm -f -- "$FAVORITES_CACHE_FILE"
    find "$THUMB_DIR" -maxdepth 1 -type f -name '*.png' -delete
    notify "No supported wallpapers found." "$WALLPAPER_DIR"
    log INFO "Only unsupported wallpaper names were found."
    return 1
  fi

  ((SHOW_PROGRESS)) && [[ -t 2 ]] && render_progress=1

  if ((render_progress)); then
    printf '\n' >&2
    PROGRESS_ACTIVE=1

    if ! printf '%s\0' "${valid_files[@]}" |
      xargs -0 -r -n 1 -P "$MAX_JOBS" bash -c '
        set -Eeuo pipefail
        generate_thumb_safe "$1" >/dev/null
        printf "%s\n" "."
      ' _ |
      gawk -v total="${#valid_files[@]}" '
        BEGIN { start = systime() }
        {
          c++
          p = int((c / total) * 100)
          e = systime() - start
          eta = (c > 0 && e > 0) ? (total - c) / (c / e) : 0
          bars = int(p / 2)
          str = sprintf("%*s", bars, "")
          gsub(/ /, "#", str)
          printf "\r\033[K[\033[32m%-50s\033[0m] %d%% (%d/%d) ETA: %ds", str, p, c, total, eta
          fflush()
        }
        END { print "\n\nCache generation complete." }
      ' >&2; then
      PROGRESS_ACTIVE=0
      die "Thumbnail worker failed unexpectedly."
    fi

    PROGRESS_ACTIVE=0
  else
    if ! printf '%s\0' "${valid_files[@]}" |
      xargs -0 -r -n 1 -P "$MAX_JOBS" bash -c 'set -Eeuo pipefail; generate_thumb_safe "$1"' _; then
      die "Thumbnail worker failed unexpectedly."
    fi
  fi

  : >"$cache_tmp"

  for rel in "${valid_rels[@]}"; do
    thumb=$(thumb_path "$rel")
    [[ -f "$thumb" ]] || thumb="$PLACEHOLDER"

    printf '%s\0icon\x1f%s\x1finfo\x1f%s\n' \
      "${rel##*/}" "$thumb" "$rel" >>"$cache_tmp"
  done

  mv -f -- "$cache_tmp" "$CACHE_FILE"
  mv -f -- "$state_tmp" "$SOURCE_STATE_FILE"
  cleanup_orphan_thumbs valid_rels
  rm -f -- "$FAVORITES_CACHE_FILE"

  if ((skipped > 0)); then
    notify \
      "Skipped unsupported wallpaper names." \
      "${skipped} file(s) contain tabs, newlines, carriage returns, unit separators, or unsafe path components."
  fi

  log INFO "Wallpaper cache built: ${#valid_rels[@]} supported, ${skipped} skipped."
  return 0
}

collect_favorites() {
  # shellcheck disable=SC2178
  local -n favorites_ref=$1
  local fav
  local -A seen=()

  favorites_ref=()

  [[ -f "$FAVORITES_FILE" ]] || return 1

  while IFS= read -r fav || [[ -n $fav ]]; do
    [[ -n $fav ]] || continue
    is_supported_rel_path "$fav" || continue
    [[ -f "$WALLPAPER_DIR/$fav" ]] || continue
    [[ ${seen[$fav]+_} ]] && continue

    seen["$fav"]=1
    favorites_ref+=("$fav")
  done <"$FAVORITES_FILE"

  return 0
}

write_favorites_file() {
  # shellcheck disable=SC2178
  local -n favorites_ref=$1
  local tmp fav

  tmp=$(mktemp --tmpdir="$SETTINGS_DIR" wal_fav_rofi.tmp.XXXXXX)
  register_temp "$tmp"
  : >"$tmp"

  for fav in "${favorites_ref[@]}"; do
    printf '%s\n' "$fav" >>"$tmp"
  done

  # Atomic write ported from theme_ctl.sh
  mv -fT -- "$tmp" "$FAVORITES_FILE"
  return 0
}

build_favorites_cache() {
  local -a favorites=()
  local rel thumb tmp

  collect_favorites favorites || true
  write_favorites_file favorites

  if ((${#favorites[@]} == 0)); then
    rm -f -- "$FAVORITES_CACHE_FILE"
    notify "No liked wallpapers found."
    log INFO "No liked wallpapers found."
    return 1
  fi

  tmp=$(mktemp --tmpdir="$CACHE_DIR" rofi_input_fav.cache.tmp.XXXXXX)
  register_temp "$tmp"
  : >"$tmp"

  for rel in "${favorites[@]}"; do
    generate_thumb "$WALLPAPER_DIR/$rel" || true
    thumb=$(thumb_path "$rel")
    [[ -f "$thumb" ]] || thumb="$PLACEHOLDER"

    printf '%s\0icon\x1f%s\x1finfo\x1f%s\n' \
      "${rel##*/}" "$thumb" "$rel" >>"$tmp"
  done

  # Atomic write ported from theme_ctl.sh
  mv -fT -- "$tmp" "$FAVORITES_CACHE_FILE"
  printf '%s\n' "$FAVORITES_CACHE_FILE"
  return 0
}

toggle_favorite() {
  local rel=$1
  local -a favorites=()
  local -a updated=()
  local fav
  local removed=0

  is_supported_rel_path "$rel" || {
    notify "Cannot like this wallpaper." "$rel"
    log WARN "Rejected favorite toggle for unsupported path: $rel"
    return 1
  }

  [[ -f "$WALLPAPER_DIR/$rel" ]] || {
    notify "Cannot like missing wallpaper." "$rel"
    log WARN "Rejected favorite toggle for missing file: $rel"
    return 1
  }

  collect_favorites favorites || true

  for fav in "${favorites[@]}"; do
    if [[ $fav == "$rel" ]]; then
      removed=1
      continue
    fi
    updated+=("$fav")
  done

  if ((removed == 0)); then
    updated+=("$rel")
    write_favorites_file updated
    rm -f -- "$FAVORITES_CACHE_FILE"
    notify "Liked Wallpaper" "$rel"
    log INFO "Liked wallpaper: $rel"
  else
    write_favorites_file updated
    rm -f -- "$FAVORITES_CACHE_FILE"
    notify "Unliked Wallpaper" "$rel"
    log INFO "Unliked wallpaper: $rel"
  fi

  return 0
}

resolve_path() {
  local rel=$1
  local full_path

  is_supported_rel_path "$rel" || return 1

  full_path="$WALLPAPER_DIR/$rel"
  [[ -f "$full_path" ]] || return 1

  printf '%s\n' "$full_path"
}

cache_info_by_index() {
  local input=$1
  local index=$2
  local target=$((index + 1))

  gawk -v target="$target" '
    BEGIN {
      key = sprintf("%cinfo%c", 31, 31)
    }
    NR == target {
      pos = index($0, key)
      if (pos == 0) {
        exit 1
      }
      found = 1
      print substr($0, pos + length(key))
      exit 0
    }
    END {
      if (!found) {
        exit 1
      }
    }
  ' "$input"
}

get_active_wallpaper_filename() {
  local awww_out current_image

  # UWSM Wrap applied
  # FIX: Read the entire command output stream because `awww query` 
  # often emits daemon status text on line 1 and the image on line 2+.
  while IFS= read -r awww_out; do
    if [[ $awww_out == *image:* ]]; then
      current_image=${awww_out##*image: }
      current_image="${current_image#"${current_image%%[![:space:]]*}"}"
      current_image="${current_image%"${current_image##*[![:space:]]}"}"

      printf '%s\n' "${current_image##*/}"
      return 0
    fi
  # Fallback to direct awww call just in case UWSM parsing fails
  done < <(uwsm-app -- awww query 2>/dev/null || awww query 2>/dev/null)

  return 1
}

# -----------------------------------------------------------------------------
# CORE GEOMETRIC MATRIX
# -----------------------------------------------------------------------------
get_dynamic_theme_str() {
  if command -v hyprctl >/dev/null 2>&1 && command -v jq >/dev/null 2>&1; then
    local mon_info logical_width logical_height

    # Extract robust logical integers accurately taking scaling into account
    mon_info=$(hyprctl monitors -j 2>/dev/null | jq -r '.[] | select(.focused == true) | "\(.width / .scale | floor) \(.height / .scale | floor)"')
    
    read -r logical_width logical_height <<< "$mon_info"

    if [[ -n "$logical_width" && -n "$logical_height" && "$logical_width" =~ ^[0-9]+$ && "$logical_height" =~ ^[0-9]+$ ]]; then
      local icon_size window_width_pct

      # Epistemological constraint: We enforce a strict, immutable 4x2 topological matrix.
      # To eradicate the spatial hypertrophy and nullify extraneous abyssal gaps at the 
      # inferior boundary, we calculate icon dimensions strictly as a modest fraction of 
      # the logical vertical axis, eschewing earlier cross-axis conflations.
      icon_size=$(( (logical_height * 16) / 100 )) 
      
      # Bound the scalar to preserve absolute orthographic elegance.
      (( icon_size < 100 )) && icon_size=100
      (( icon_size > 220 )) && icon_size=220

      # Enforce a constrained, sophisticated window width footprint. We dynamically 
      # broaden the container strictly if the viewport transverses into portrait orientation.
      if (( logical_width >= logical_height )); then
        window_width_pct=50
      else
        window_width_pct=85
      fi

      # The injection of `fixed-columns: true` and `fixed-height: true` structurally 
      # amputates the renderer's propensity for generating vacant terminal space, 
      # enforcing an unyielding 8-item spatial hegemony.
      printf "%s" "window { width: ${window_width_pct}%; } listview { columns: 4; lines: 2; fixed-columns: true; fixed-height: true; } element-icon { size: ${icon_size}px; }"
      return 0
    fi
  fi
  printf ""
}

# --- STATE TRACKING & ANIMATION SYNC ---

get_theme_mode() {
  local mode="dark"
  if [[ -f "$STATE_FILE" ]]; then
    local val
    val=$(grep '^THEME_MODE=' "$STATE_FILE" | cut -d= -f2 | tr -d '"'\''' || true)
    [[ -n "$val" ]] && mode="$val"
  fi
  printf "%s" "$mode"
}

get_animation_state() {
  if [[ -f "$STATE_FILE" ]]; then
    while IFS='=' read -r key val; do
      val="${val%\"}"; val="${val#\"}" 
      val="${val%\'}"; val="${val#\'}"
      case "$key" in
        AWWW_TRANS_TYPE)     CUR_T_TYPE="$val" ;;
        AWWW_TRANS_DURATION) CUR_T_DUR="$val" ;;
        AWWW_TRANS_FPS)      CUR_T_FPS="$val" ;;
        AWWW_TRANS_BEZIER)   CUR_T_BEZ="$val" ;;
        AWWW_TRANS_ANGLE)    CUR_T_ANG="$val" ;;
        AWWW_TRANS_POS)      CUR_T_POS="$val" ;;
      esac
    done < <(grep -E '^[A-Z_]+=' "$STATE_FILE" 2>/dev/null || true)
  fi
}

update_tracker() {
  local rel_path="$1"
  local mode
  mode=$(get_theme_mode)
  local track_file="${SETTINGS_DIR}/dusky_theme/${mode}_wal"
  
  # Atomic write ported from theme_ctl.sh
  local tmp_track
  tmp_track=$(mktemp "${SETTINGS_DIR}/dusky_theme/track.tmp.XXXXXX")
  register_temp "$tmp_track"

  printf "%s\n" "${rel_path##*/}" > "$tmp_track"
  mv -fT -- "$tmp_track" "$track_file"
}

update_fav_state() {
  local fav_rel="$1"
  
  # Atomic write ported from theme_ctl.sh
  local tmp_state
  tmp_state=$(mktemp "${SETTINGS_DIR}/dusky_theme/current_fav.tmp.XXXXXX")
  register_temp "$tmp_state"
  
  printf '%s\n' "${fav_rel##*/}" > "$tmp_state"
  mv -fT -- "$tmp_state" "$FAV_STATE_FILE"
}

cycle_next_favorite() {
  local -a favs=()
  collect_favorites favs || true

  if (( ${#favs[@]} == 0 )); then
    notify "No Favorites" "You haven't liked any wallpapers yet."
    log INFO "Cannot cycle favorites: list is empty."
    exit 0
  fi

  local current_fav=""
  if [[ -f "$FAV_STATE_FILE" ]]; then
    current_fav=$(<"$FAV_STATE_FILE")
  fi

  local -a sorted_favs=()
  # Null-delimited (-z) array population + Version sort (-V) ported from theme_ctl.sh
  mapfile -d '' sorted_favs < <(printf '%s\0' "${favs[@]}" | LC_ALL=C sort -z -V)

  local next_fav="${sorted_favs[0]}"
  local -i i=0
  local -i len=${#sorted_favs[@]}

  if [[ -n "$current_fav" ]]; then
    for (( i=0; i<len; i++ )); do
      if [[ "${sorted_favs[$i]##*/}" == "$current_fav" ]]; then
        local next_idx=$(( (i + 1) % len ))
        next_fav="${sorted_favs[$next_idx]}"
        break
      fi
    done
  fi

  update_fav_state "$next_fav"
  
  local full_path="$WALLPAPER_DIR/$next_fav"
  log INFO "Cycling to next favorite: $next_fav"
  
  apply_selection "$full_path" "$next_fav" "REGEN"
  exit 0
}

show_menu() {
  local mode=$1
  local input=$2
  local selection=${3:-}
  local prompt message
  local new_selection=""
  local next_input=""
  local exit_code
  local -a rofi_cmd

  while true; do
    # Pango-styled, ultra-compact UI header to prevent the layout from feeling crammed.
    if [[ $mode == favorites ]]; then
      prompt="Liked"
      message="<span size='small' color='#999999'><b>[Enter]</b> Apply   <b>[Alt+H]</b> Fast Apply   <b>[Alt+U]</b> Unlike   <b>[Alt+T]</b> View All   <b>[Alt+Y]</b> Rebuild Cache</span>"
    else
      prompt="Wallpaper"
      message="<span size='small' color='#999999'><b>[Enter]</b> Apply   <b>[Alt+H]</b> Fast Apply   <b>[Alt+U]</b> Like/Unlike   <b>[Alt+T]</b> View Liked   <b>[Alt+Y]</b> Rebuild Cache</span>"
    fi

    # UWSM Wrap applied with Custom Keybindings mapping
    # FIX: -no-fixed-num-lines acts as the primary shrink-wrap enforcer forcing Rofi
    # to perfectly hug the items instead of drawing 100 empty placeholder lines!
    rofi_cmd=(
      uwsm-app -- rofi
      -dmenu
      -no-custom
      -i
      -show-icons
      -format i
      -no-fixed-num-lines
      -p "$prompt"
      -mesg "$message"
      -kb-custom-1 "Alt+u"
      -kb-custom-2 "Alt+y"
      -kb-custom-3 "Alt+t"
      -kb-custom-4 "Alt+h"
    )

    if [[ -f "$ROFI_THEME" ]]; then
      rofi_cmd+=(-theme "$ROFI_THEME")
    fi

    local dynamic_theme
    dynamic_theme=$(get_dynamic_theme_str)
    if [[ -n "$dynamic_theme" ]]; then
        rofi_cmd+=("-theme-str" "$dynamic_theme")
    fi

    if [[ -n $selection ]]; then
      rofi_cmd+=(-select "${selection##*/}")
    fi

    if new_selection="$("${rofi_cmd[@]}" <"$input")"; then
      exit_code=0
    else
      exit_code=$?
    fi

    if [[ -n $new_selection && $new_selection =~ ^[0-9]+$ ]]; then
      selection=$(cache_info_by_index "$input" "$new_selection" || true)
    else
      selection=""
    fi

    case $exit_code in
      0)
        # Standard Apply (With Matugen Regen)
        [[ -n $selection ]] || return 1
        printf 'REGEN\n%s\n' "$selection"
        return 0
        ;;
      1)
        # User Hit Escape
        return 1
        ;;
      10)
        # Alt+U -> Toggle Liked
        [[ -n $selection ]] || continue
        toggle_favorite "$selection" || true

        if [[ $mode == favorites ]]; then
          if next_input=$(build_favorites_cache); then
            input="$next_input"
          else
            mode="all"
            input="$CACHE_FILE"
            selection=""
          fi
        fi
        ;;
      11)
        # Alt+Y -> Rebuild Cache
        build_cache || true

        if [[ $mode == favorites ]]; then
          if next_input=$(build_favorites_cache); then
            input="$next_input"
          else
            mode="all"
            input="$CACHE_FILE"
            selection=""
          fi
        else
          input="$CACHE_FILE"
          [[ -s $input ]] || return 1
        fi
        ;;
      12)
        # Alt+T -> Toggle Favorites View
        selection=""
        if [[ $mode == all ]]; then
          if next_input=$(build_favorites_cache); then
            input="$next_input"
            mode="favorites"
          else
            input="$CACHE_FILE"
            mode="all"
            continue
          fi
        else
          mode="all"
          input="$CACHE_FILE"
          [[ -s $input ]] || return 1
        fi
        ;;
      13)
        # Alt+H -> Apply Without Matugen Regen
        [[ -n $selection ]] || continue
        printf 'NO_REGEN\n%s\n' "$selection"
        return 0
        ;;
      *)
        log WARN "Rofi exited with unexpected code: $exit_code"
        return 1
        ;;
    esac
  done
}

apply_selection() {
  local full_path=$1
  local selection=$2
  local action=$3
  local output

  log INFO "Applying wallpaper: $full_path (Mode: $action)"

  # Sync tracker BEFORE theme_ctl runs so chronological order is maintained
  update_tracker "$selection"
  update_fav_state "$selection"

  # Dynamically pull the user's configured animation state
  get_animation_state

  # Construct the precise awww command matching the wizard settings
  local -a awww_cmd=(uwsm-app -- awww img)
  [[ -n "$CUR_T_TYPE" && "$CUR_T_TYPE" != "disable" ]] && awww_cmd+=(--transition-type "$CUR_T_TYPE")
  [[ -n "$CUR_T_DUR"  && "$CUR_T_DUR"  != "disable" ]] && awww_cmd+=(--transition-duration "$CUR_T_DUR")
  [[ -n "$CUR_T_FPS"  && "$CUR_T_FPS"  != "disable" ]] && awww_cmd+=(--transition-fps "$CUR_T_FPS")
  [[ -n "$CUR_T_BEZ"  && "$CUR_T_BEZ"  != "disable" ]] && awww_cmd+=(--transition-bezier "$CUR_T_BEZ")
  [[ -n "$CUR_T_ANG"  && "$CUR_T_ANG"  != "disable" ]] && awww_cmd+=(--transition-angle "$CUR_T_ANG")
  [[ -n "$CUR_T_POS"  && "$CUR_T_POS"  != "disable" ]] && awww_cmd+=(--transition-pos "$CUR_T_POS")
  
  awww_cmd+=("$full_path")

  if ! output=$("${awww_cmd[@]}" 2>&1); then
    die "Failed to set wallpaper." "$output"
  fi

  [[ -n $output ]] && log_output INFO "awww: " "$output"

  # If Alt+H was pressed, exit here before Matugen engages.
  if [[ "$action" == "NO_REGEN" ]]; then
    notify "Wallpaper Applied" "Skipped Matugen regeneration."
    return 0
  fi

  if [[ ! -x "$THEME_CTL" ]]; then
    die "Theme controller not found or not executable." "$THEME_CTL"
  fi

  log INFO "Triggering theme_ctl to synchronize Matugen..."
  notify "Generating Theme" "Matugen is processing colors..." "low" "2500"
  
  if ! output=$("$THEME_CTL" refresh 2>&1); then
    die "Failed to apply theme via theme_ctl." "$output"
  fi

  [[ -n $output ]] && log_output INFO "theme_ctl: " "$output"
}

ensure_cache() {
  if ((FORCE_REBUILD)); then
    log INFO "Forced cache rebuild requested."
    build_cache || true
    return 0
  fi

  if cache_is_current; then
    log INFO "Wallpaper cache is current."
    return 0
  fi

  log INFO "Wallpaper cache is missing or stale. Rebuilding."
  build_cache || true
}

main() {
  setup_logging
  trap on_err ERR

  parse_args "$@"
  check_dependencies
  initialize_runtime
  validate_config
  acquire_lock
  ensure_placeholder

  if ((CYCLE_FAV)); then
    release_lock
    cycle_next_favorite
  fi

  ensure_cache

  if ((CACHE_ONLY)); then
    exit 0
  fi

  if [[ ! -s "$CACHE_FILE" ]]; then
    log INFO "No wallpaper entries to display."
    exit 0
  fi

  local mode="all"
  local input="$CACHE_FILE"
  local next_input=""
  local selection
  local action
  local full_path
  local active_wal=""
  local menu_output=""

  active_wal=$(get_active_wallpaper_filename) || true

  if ((SHOW_FAVORITES)); then
    mode="favorites"
    if next_input=$(build_favorites_cache); then
      input="$next_input"
    else
      exit 0
    fi
  fi

  if ! menu_output=$(show_menu "$mode" "$input" "$active_wal"); then
    log INFO "Menu closed without selection."
    exit 0
  fi

  # Safely parse the multiline return: Line 1 = Action, Line 2 = File
  action=$(sed -n '1p' <<< "$menu_output")
  selection=$(sed '1d' <<< "$menu_output")

  log INFO "Selected wallpaper: $selection (Action: $action)"

  if ! full_path=$(resolve_path "$selection"); then
    die "Failed to resolve wallpaper path." "$selection"
  fi

  # Release the lock BEFORE spawning awww/matugen and their background hooks
  release_lock

  apply_selection "$full_path" "$selection" "$action"
  log INFO "Wallpaper applied successfully."
}

main "$@"
exit 0
