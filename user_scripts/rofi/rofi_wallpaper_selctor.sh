#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# 🖼️ ROFI WALLPAPER SELECTOR (Robust V2)
# Features:
#   - V2 Cache (Forces fresh build to fix "no preview" issues)
#   - Solid Placeholder (No font dependencies)
#   - Explicit Icon Mode
# -----------------------------------------------------------------------------

set -u
set -o pipefail

# --- CONFIGURATION ---
readonly WALLPAPER_DIR="${HOME}/Pictures/wallpapers"
readonly CACHE_DIR="${HOME}/.cache/rofi-wallpaper-thumbs"
# CHANGED: v2 suffix forces a fresh cache rebuild
readonly CACHE_FILE="${CACHE_DIR}/rofi_input_v2.cache"
readonly PATH_MAP="${CACHE_DIR}/path_map.cache"
readonly PLACEHOLDER_FILE="${CACHE_DIR}/_placeholder.png"
readonly ROFI_THEME="${HOME}/.config/rofi/wallpaper.rasi"
readonly RANDOM_THEME_SCRIPT="${HOME}/user_scripts/random_theme.sh"
readonly THUMB_SIZE=300

# Parallel jobs: number of cores * 2
readonly MAX_JOBS=$(($(nproc) * 2))

# Dependencies
for cmd in magick rofi swww notify-send; do
    if ! command -v "$cmd" &>/dev/null; then
        notify-send "Error" "Missing dependency: $cmd" -u critical
        exit 1
    fi
done

mkdir -p "$CACHE_DIR"

# --- FUNCTIONS ---

ensure_placeholder() {
    # Simplified: Just a solid dark gray square. No text/fonts to fail.
    if [[ ! -f "$PLACEHOLDER_FILE" ]]; then
        magick -size "${THUMB_SIZE}x${THUMB_SIZE}" xc:"#333333" \
            "$PLACEHOLDER_FILE" 2>/dev/null
    fi
}

# Worker function for parallel execution
generate_single_thumb() {
    local file="$1"
    local filename="${file##*/}"
    local thumb="${CACHE_DIR}/${filename}.png"
    
    # If thumb exists and is newer than the image, skip
    [[ -f "$thumb" && "$thumb" -nt "$file" ]] && return 0
    
    # Generate thumb
    nice -n 19 magick "$file" \
        -strip \
        -resize "${THUMB_SIZE}x${THUMB_SIZE}^" \
        -gravity center \
        -extent "${THUMB_SIZE}x${THUMB_SIZE}" \
        "$thumb" 2>/dev/null
}
export -f generate_single_thumb
export CACHE_DIR THUMB_SIZE

# Clean up thumbs for wallpapers that were deleted
cleanup_orphans() {
    for thumb in "$CACHE_DIR"/*.png; do
        filename=$(basename "$thumb")
        [[ "$filename" == "_placeholder.png" ]] && continue
        
        if ! grep -q "^${filename%.png}" "$PATH_MAP" 2>/dev/null; then
             rm -f "$thumb"
        fi
    done
}

refresh_cache() {
    notify-send -a "Wallpaper Menu" "Refreshing Wallpaper cache" "Please wait. CPU usage may be high during this process." -u low -t 1000
    ensure_placeholder
    
    # 1. Update Thumbnails (Parallel)
    find "$WALLPAPER_DIR" -type f \( \
        -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.png" \
        -o -iname "*.webp" -o -iname "*.gif" \
    \) -print0 | xargs -0 -P "$MAX_JOBS" -I {} bash -c 'generate_single_thumb "$@"' _ {}
    
    # 2. Build Cache Files
    : > "$CACHE_FILE"
    : > "$PATH_MAP"
    
    while IFS= read -r -d '' file; do
        filename=$(basename "$file")
        thumb="${CACHE_DIR}/${filename}.png"
        
        if [[ -f "$thumb" ]]; then
            icon="$thumb"
        else
            icon="$PLACEHOLDER_FILE"
        fi
        
        # Format: Name \0 icon \x1f PathToIcon
        printf '%s\0icon\x1f%s\n' "$filename" "$icon" >> "$CACHE_FILE"
        
        # Map: Name -> FullPath
        printf '%s\t%s\n' "$filename" "$file" >> "$PATH_MAP"
        
    done < <(find "$WALLPAPER_DIR" -type f \( \
        -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.png" \
        -o -iname "*.webp" -o -iname "*.gif" \
    \) -print0 | sort -z)
    
    ( cleanup_orphans ) & disown
}

get_matugen_flags() {
    if [[ -f "$RANDOM_THEME_SCRIPT" ]]; then
        grep -oP 'matugen \K.*(?= image)' "$RANDOM_THEME_SCRIPT" | head -n 1
    else
        echo ""
    fi
}

resolve_path() {
    local name="$1"
    awk -F'\t' -v t="$name" '$1 == t {print $2; exit}' "$PATH_MAP"
}

# --- MAIN LOGIC ---

# Force refresh if v2 cache is missing
if [[ ! -s "$CACHE_FILE" ]] || [[ "$WALLPAPER_DIR" -nt "$CACHE_FILE" ]]; then
    refresh_cache
fi

# Launch Rofi
# Added -show-icons explicitly to be safe
selection=$(rofi \
    -dmenu \
    -i \
    -show-icons \
    -theme "$ROFI_THEME" \
    -p "Wallpaper" \
    < "$CACHE_FILE"
)

exit_code=$?

if [[ $exit_code -ne 0 ]]; then
    exit 0
fi

if [[ -n "$selection" ]]; then
    full_path=$(resolve_path "$selection")
    
    if [[ -n "$full_path" && -f "$full_path" ]]; then
        current_flags=$(get_matugen_flags)
        [[ -z "$current_flags" ]] && current_flags="--mode dark"

        echo "Applying: $full_path"
        
        swww img "$full_path" \
            --transition-type grow \
            --transition-duration 2 \
            --transition-fps 60 &
            
        setsid uwsm-app -- matugen $current_flags image "$full_path" &
    else
        # If path resolution failed, cache might be corrupted. Delete it.
        rm -f "$CACHE_FILE"
        notify-send "Error" "Could not resolve path. Cache cleared." -u critical
    fi
fi
