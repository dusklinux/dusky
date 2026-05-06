#!/usr/bin/env bash
# Plymouth Asset Optimizer - Elite DevOps Edition
# Execution Context: Run directly inside the target assets directory.

#---------------------
#  type `identify *` in the current dir for info on the alpha of the images
#---------------------
set -Eeuo pipefail
export LC_ALL=C

# 1. Dynamic Context
readonly ASSETS_DIR="$(pwd)"
readonly BACKUP_DIR="${ASSETS_DIR}/.raw_backup_$(date +%s)"

# 2. Dependency Audit
if ! command -v mogrify >/dev/null 2>&1; then
    printf "\033[1;31m[FATAL]\033[0m ImageMagick is required. Run: sudo pacman -S imagemagick\n" >&2
    exit 1
fi

printf "\033[1;34m[*]\033[0m Auditing assets in: %s\n" "$ASSETS_DIR"

# 3. Pre-Flight Validation
declare -a required_assets=("logo.png" "progress_box.png" "progress_bar.png" "bullet.png" "lock.png" "entry.png")
for asset in "${required_assets[@]}"; do
    if [[ ! -f "$ASSETS_DIR/$asset" ]]; then
        printf "\033[1;31m[FATAL]\033[0m Missing expected asset: %s. Are you running this in the right directory?\n" "$asset" >&2
        exit 1
    fi
done

# 4. Fail-Safe Archival
printf "\033[1;34m[*]\033[0m Creating non-destructive raw backup in: %s\n" "$BACKUP_DIR"
mkdir -p "$BACKUP_DIR"
cp -a "$ASSETS_DIR"/*.png "$BACKUP_DIR/"

# 5. Destructive Optimization Pipeline
printf "\033[1;34m[*]\033[0m Commencing pixel-perfect optimization...\n"

printf "    -> Crushing logo.png (Bounding box: 500x500 max)...\n"
mogrify -trim +repage -resize '500x500>' "$ASSETS_DIR/logo.png"

printf "    -> Stripping and locking Progress UI width (Box: 554px, Bar: 539px)...\n"
mogrify -trim +repage -resize '554x>' "$ASSETS_DIR/progress_box.png"
mogrify -trim +repage -resize '539x>' "$ASSETS_DIR/progress_bar.png"

printf "    -> Crushing initramfs memory-leak (bullet.png -> 14x14 max)...\n"
mogrify -trim +repage -resize '14x14>' "$ASSETS_DIR/bullet.png"

printf "    -> Trimming LUKS UI dead-space...\n"
mogrify -trim +repage "$ASSETS_DIR/lock.png"
mogrify -trim +repage "$ASSETS_DIR/entry.png"

printf "\033[1;32m[SUCCESS]\033[0m Assets mathematically optimized. Ready for git commit.\n"
printf "Note: If you need to revert, your originals are in the hidden %s directory.\n" "$BACKUP_DIR"
