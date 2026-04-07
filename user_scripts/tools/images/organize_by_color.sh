#!/usr/bin/env bash
set -euo pipefail
# ── Colors ──────────────────────────────────────────────────────────────
RED=$'\033[0;31m'    GREEN=$'\033[0;32m'  YELLOW=$'\033[0;33m'
BLUE=$'\033[0;34m'   MAGENTA=$'\033[0;35m' CYAN=$'\033[0;36m'
BOLD=$'\033[1m'      RESET=$'\033[0m'
# ── Helpers ─────────────────────────────────────────────────────────────
info()    { printf "${BLUE}[INFO]${RESET}  %s\n" "$*"; }
warn()    { printf "${YELLOW}[WARN]${RESET}  %s\n" "$*"; }
err()     { printf "${RED}[ERROR]${RESET} %s\n" "$*" >&2; }
success() { printf "\n${BOLD}${GREEN}✓ %s${RESET}\n" "$*"; }
# ── Defaults ────────────────────────────────────────────────────────────
SRC_DIR="."
DRY_RUN=false
FORCE=false
UNDO=false
# ── Parse arguments ─────────────────────────────────────────────────────
usage() {
    cat <<EOF
${BOLD}Usage:${RESET} $(basename "$0") [OPTIONS] [DIRECTORY]
Rename wallpaper files by prepending their dominant color tag.
  e.g. 0001.jpg → blue_0001.jpg
${BOLD}Options:${RESET}
  -n, --dry-run   Show what would be done without renaming files
  -f, --force     Overwrite if a target filename already exists
  -u, --undo      Strip existing color prefixes from filenames
  -h, --help      Show this help message
${BOLD}Color tags:${RESET}
  blue  red  green  orange  yellow  purple  pink  cyan  brown  noir
${BOLD}Examples:${RESET}
  $(basename "$0")                          # Tag files in current directory
  $(basename "$0") -n                       # Preview only (recommended first!)
  $(basename "$0") -u                       # Undo: strip color prefixes
EOF
}
while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--dry-run) DRY_RUN=true; shift ;;
        -f|--force)   FORCE=true; shift ;;
        -u|--undo)    UNDO=true; shift ;;
        -h|--help)    usage; exit 0 ;;
        -*)           err "Unknown option: $1"; usage; exit 1 ;;
        *)            SRC_DIR="$1"; shift ;;
    esac
done
# ── Validate ────────────────────────────────────────────────────────────
if [[ ! -d "$SRC_DIR" ]]; then
    err "Directory not found: ${BOLD}$SRC_DIR${RESET}"
    exit 1
fi
SRC_DIR="$(cd "$SRC_DIR" && pwd)"
# ── Undo mode: strip color prefixes ─────────────────────────────────────
if [[ "$UNDO" == true ]]; then
    shopt -s nullglob
    PREFIXES=(blue_ red_ green_ orange_ yellow_ purple_ pink_ cyan_ brown_ noir_)
    RENAMED=0
    for prefix in "${PREFIXES[@]}"; do
        for f in "$SRC_DIR"/${prefix}*; do
            [[ -f "$f" ]] || continue
            base=$(basename "$f")
            stripped="${base#$prefix}"
            [[ "$stripped" == "$base" ]] && continue
            target="$SRC_DIR/$stripped"
            if [[ "$DRY_RUN" == true ]]; then
                printf "  ${CYAN}%s${RESET} → ${GREEN}%s${RESET}\n" "$base" "$stripped"
            elif [[ -e "$target" && "$FORCE" != true ]]; then
                warn "Target exists: ${BOLD}$stripped${RESET} — skipping"
                continue
            else
                mv "$f" "$target"
            fi
            ((RENAMED++)) || true
        done
    done
    shopt -u nullglob
    printf "\n"
    if [[ "$DRY_RUN" == true ]]; then
        info "Dry run — ${BOLD}$RENAMED${RESET} file(s) would be untagged"
    else
        success "$RENAMED file(s) untagged"
    fi
    exit 0
fi
# ── Collect image files (case-insensitive extensions) ───────────────────
shopt -s nullglob nocaseglob
FILES=("$SRC_DIR"/*.jpg "$SRC_DIR"/*.jpeg "$SRC_DIR"/*.png "$SRC_DIR"/*.bmp "$SRC_DIR"/*.webp)
shopt -u nullglob nocaseglob
if [[ ${#FILES[@]} -eq 0 ]]; then
    warn "No supported image files found in ${BOLD}$SRC_DIR${RESET}"
    exit 0
fi
info "Found ${BOLD}${#FILES[@]}${RESET} image(s) in ${BOLD}$SRC_DIR${RESET}"
# ── Temp files (cleaned up on exit) ─────────────────────────────────────
PYSCRIPT=$(mktemp --suffix=.py)
RESULTS=$(mktemp)
trap 'rm -f "$PYSCRIPT" "$RESULTS"' EXIT
# ── Write Python classifier script ──────────────────────────────────────
cat > "$PYSCRIPT" <<'PYEOF'
import sys
from PIL import Image
import numpy as np
CATEGORIES = [
    ("red",    lambda h: (h < 15) | (h >= 345)),
    ("orange", lambda h: (h >= 15)  & (h < 40)),
    ("yellow", lambda h: (h >= 40)  & (h < 65)),
    ("green",  lambda h: (h >= 65)  & (h < 165)),
    ("cyan",   lambda h: (h >= 165) & (h < 200)),
    ("blue",   lambda h: (h >= 200) & (h < 260)),
    ("purple", lambda h: (h >= 260) & (h < 300)),
    ("pink",   lambda h: (h >= 300) & (h < 345)),
]
SAT_GRAY_THRESHOLD = 0.12
GRAY_RATIO_THRESH  = 0.50
BROWN_V_THRESHOLD  = 0.38
BROWN_RATIO_THRESH = 0.55
THUMB_SIZE = (64, 64)
def classify(path):
    try:
        img = Image.open(path).convert("RGB")
    except Exception:
        return "unknown"
    img.thumbnail(THUMB_SIZE, Image.LANCZOS)
    hsv = np.array(img.convert("HSV"), dtype=np.float64)
    h = hsv[:, :, 0] * 360.0 / 255.0
    s = hsv[:, :, 1] / 255.0
    v = hsv[:, :, 2] / 255.0
    total = h.shape[0] * h.shape[1]
    gray_mask = s < SAT_GRAY_THRESHOLD
    gray_ratio = np.count_nonzero(gray_mask) / total
    if gray_ratio > GRAY_RATIO_THRESH:
        return "noir"
    colored = ~gray_mask
    h_colored = h[colored]
    if h_colored.size == 0:
        return "noir"
    counts = {}
    for name, hue_fn in CATEGORIES:
        counts[name] = int(np.count_nonzero(hue_fn(h_colored)))
    orange_mask_colored = (h_colored >= 15) & (h_colored < 40)
    if np.count_nonzero(orange_mask_colored) > 0:
        orange_v = v[colored][orange_mask_colored]
        dark_ratio = np.count_nonzero(orange_v < BROWN_V_THRESHOLD) / orange_v.size
        if dark_ratio > BROWN_RATIO_THRESH:
            counts["brown"] = counts.pop("orange")
    best_cat = max(counts, key=counts.get)
    best_count = counts[best_cat]
    if best_count < total * 0.05:
        return "noir"
    return best_cat
for line in sys.stdin:
    path = line.strip()
    if not path:
        continue
    category = classify(path)
    print(f"{path}\t{category}")
PYEOF
# ── Classify all images ─────────────────────────────────────────────────
printf '%s\n' "${FILES[@]}" | python3 "$PYSCRIPT" > "$RESULTS"
# ── Build category sets ─────────────────────────────────────────────────
declare -A CATEGORY_COUNTS
CATEGORIES_FOUND=()
while IFS=$'\t' read -r filepath category; do
    [[ -z "$filepath" ]] && continue
    if [[ -z "${CATEGORY_COUNTS[$category]+_}" ]]; then
        CATEGORY_COUNTS[$category]=0
        CATEGORIES_FOUND+=("$category")
    fi
done < "$RESULTS"
# ── Rename files ────────────────────────────────────────────────────────
MOVED=0
SKIPPED=0
FAILED=0
COLOR_PREFIXES=("blue_" "red_" "green_" "orange_" "yellow_" "purple_" "pink_" "cyan_" "brown_" "noir_")
while IFS=$'\t' read -r filepath category; do
    [[ -z "$filepath" ]] && continue
    filename=$(basename "$filepath")
    if [[ "$DRY_RUN" == true ]]; then
        already_tagged=false
        for prefix in "${COLOR_PREFIXES[@]}"; do
            if [[ "$filename" == ${prefix}* ]]; then
                already_tagged=true
                break
            fi
        done
        if [[ "$already_tagged" == true ]]; then
            printf "  ${CYAN}%s${RESET}  ${BOLD}(already tagged)${RESET}\n" "$filename"
        else
            newname="${category}_${filename}"
            printf "  ${CYAN}%s${RESET} → ${GREEN}%s${RESET}\n" "$filename" "$newname"
        fi
        ((MOVED++)) || true
        continue
    fi
    if [[ "$category" == "unknown" ]]; then
        warn "Could not classify: ${BOLD}$filename${RESET} — skipping"
        ((FAILED++)) || true
        continue
    fi
    # Check if file already has a color prefix
    existing_prefix=""
    base_name="$filename"
    for prefix in "${COLOR_PREFIXES[@]}"; do
        if [[ "$filename" == ${prefix}* ]]; then
            existing_prefix="$prefix"
            base_name="${filename#$prefix}"
            break
        fi
    done
    # Skip if already correctly tagged with the same color
    if [[ "$existing_prefix" == "${category}_" ]]; then
        ((SKIPPED++)) || true
        continue
    fi
    # Build target name
    if [[ -n "$existing_prefix" ]]; then
        newname="${category}_${base_name}"
    else
        newname="${category}_${filename}"
    fi
    target="$SRC_DIR/$newname"
    if [[ -e "$target" ]]; then
        if [[ "$FORCE" == true ]]; then
            mv -f "$filepath" "$target"
        else
            name="${base_name%.*}"
            ext="${base_name##*.}"
            counter=1
            while [[ -e "$SRC_DIR/${category}_${name}_${counter}.${ext}" ]]; do
                ((counter++))
            done
            target="$SRC_DIR/${category}_${name}_${counter}.${ext}"
            mv "$filepath" "$target"
        fi
    else
        mv "$filepath" "$target"
    fi
    ((MOVED++)) || true
done < "$RESULTS"
# ── Summary ─────────────────────────────────────────────────────────────
printf "\n${BOLD}═══════════════════════════════════════════${RESET}\n"
printf "${BOLD}  Classification Summary${RESET}\n"
printf "${BOLD}═══════════════════════════════════════════${RESET}\n\n"
for cat in "${CATEGORIES_FOUND[@]}"; do
    count=$(awk -F'\t' -v c="$cat" '$2 == c' "$RESULTS" | wc -l)
    CATEGORY_COUNTS[$cat]=$count
done
sorted_cats=$(for cat in "${CATEGORIES_FOUND[@]}"; do
    printf "%s\t%s\n" "${CATEGORY_COUNTS[$cat]}" "$cat"
done | sort -rn)
while IFS=$'\t' read -r count cat; do
    [[ -z "$cat" ]] && continue
    case "$cat" in
        red)    color="$RED" ;;
        green)  color="$GREEN" ;;
        blue)   color="$BLUE" ;;
        yellow) color="$YELLOW" ;;
        cyan)   color="$CYAN" ;;
        purple) color="$MAGENTA" ;;
        pink)   color="$MAGENTA" ;;
        orange) color=$'\033[0;33m' ;;
        brown)  color=$'\033[0;33m' ;;
        noir)   color=$'\033[1;30m' ;;
        *)      color="$RESET" ;;
    esac
    printf "  ${color}%-10s${RESET}\t${BOLD}%s${RESET}\n" "$cat" "$count"
done <<< "$sorted_cats"
printf "\n"
if [[ "$DRY_RUN" == true ]]; then
    info "Dry run — ${BOLD}$MOVED${RESET} file(s) would be renamed"
else
    success "$MOVED file(s) renamed with color tags"
    [[ $SKIPPED -gt 0 ]] && warn "$SKIPPED file(s) already tagged correctly"
    [[ $FAILED -gt 0 ]]  && warn "$FAILED file(s) could not be classified"
fi