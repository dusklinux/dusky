#!/usr/bin/env bash

# -----------------------------------------------------------------------------
# MatugenFetch Recolor - Optimized for Hyprland/UWSM
# -----------------------------------------------------------------------------
# Summary:
# 1. Validates environment and dependencies.
# 2. Extracts and normalizes colors from Matugen generation.
# 3. Invokes a single Python process to recolor all icons using NumPy.
# -----------------------------------------------------------------------------

set -euo pipefail
# Ensure subshells inherit the 'exit on error' behavior (Bash 4.4+)
shopt -s inherit_errexit

# --- Constants & Configuration ---
readonly COLOR_COUNT=4
readonly XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
readonly XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"

readonly INPUT_DIR="$XDG_CONFIG_HOME/fastfetch/pngs"
readonly OUTPUT_DIR="$INPUT_DIR/generated"
readonly COLOR_FILE="$XDG_CONFIG_HOME/matugen/generated/matugenfetch"
readonly CACHE_DIR="$XDG_CACHE_HOME/fastfetch/images"

# Base palette (immutable reference colors)
readonly -a BASE_COLORS=("#A9B1D6" "#C79BF0" "#EBBCBA" "#313244")

# --- Helpers ---
log() { printf '🔍 %s\n' "$1"; }
err() { printf '❌ Error: %s\n' "$1" >&2; }
die() { err "$1"; exit 1; }

# --- Pre-flight Checks ---
command -v python3 &>/dev/null || die "python3 not found."
# &> suppresses both stdout and stderr (tracebacks) on check failure
python3 -c "import PIL, numpy" &>/dev/null || die "Missing Python libraries: PIL (pillow) or numpy."

# Validate paths
[[ -d "$INPUT_DIR" ]] || die "Input directory missing: $INPUT_DIR"
[[ -f "$COLOR_FILE" && -r "$COLOR_FILE" ]] || die "Color file missing or unreadable: $COLOR_FILE"

# Collect PNG files (fast-fail if none exist)
shopt -s nullglob
png_files=("$INPUT_DIR"/*.png)
shopt -u nullglob

if [[ ${#png_files[@]} -eq 0 ]]; then
    die "No PNG files found in $INPUT_DIR"
fi

# --- Color Extraction ---
# Extract hex codes, normalize to uppercase with # prefix.
# We use head instead of grep -m to safely handle multiple matches per line.
mapfile -t TARGET_COLORS < <(
    grep -oE '#?[0-9a-fA-F]{6}' "$COLOR_FILE" |
    head -n "$COLOR_COUNT" |
    sed 's/^#//;s/.*/#\U&/'
)

if [[ ${#TARGET_COLORS[@]} -ne "$COLOR_COUNT" ]]; then
    die "Expected $COLOR_COUNT colors, found ${#TARGET_COLORS[@]}."
fi

log "MatugenFetch: Processing ${#png_files[@]} icons..."
log "Palette: ${BASE_COLORS[*]} -> ${TARGET_COLORS[*]}"

mkdir -p -- "$OUTPUT_DIR"

# --- Python Engine ---
# Arguments: input_dir output_dir color_count base_colors... target_colors...
python3 - "$INPUT_DIR" "$OUTPUT_DIR" "$COLOR_COUNT" "${BASE_COLORS[@]}" "${TARGET_COLORS[@]}" <<'PYTHON_EOF'
import sys
from pathlib import Path

import numpy as np
from PIL import Image

def hex_to_rgb(hex_colors):
    """Convert a list of hex color strings to a float32 NumPy array (N, 3)."""
    return np.array(
        [[int(h.lstrip('#')[i:i+2], 16) for i in (0, 2, 4)] for h in hex_colors],
        dtype=np.float32
    )

def recolor_image(src, dst, base_rgb, target_rgb, epsilon=1e-6):
    """Apply inverse-distance-weighted color mapping to an RGBA image."""
    img = Image.open(src).convert("RGBA")
    pixels = np.array(img, dtype=np.float32)

    rgb, alpha = pixels[:, :, :3], pixels[:, :, 3]
    flat = rgb.reshape(-1, 3)

    # Euclidean distance from each pixel to each base color: (N_pixels, N_colors)
    # Shape: (N, 1, 3) - (1, M, 3) -> (N, M, 3)
    diff = flat[:, np.newaxis, :] - base_rgb[np.newaxis, :, :]
    dist = np.linalg.norm(diff, axis=2)

    # Inverse distance weighting
    weights = 1.0 / (dist + epsilon)
    # Normalize weights so they sum to 1 per pixel
    weights /= weights.sum(axis=1, keepdims=True)

    # Weighted blend: (N, M) @ (M, 3) -> (N, 3)
    blended = weights @ target_rgb
    
    # Clip, round, and cast
    blended = np.clip(np.round(blended), 0, 255).astype(np.uint8)

    # Reconstruct RGBA
    result = np.dstack((blended.reshape(rgb.shape), alpha.astype(np.uint8)))
    Image.fromarray(result, "RGBA").save(dst)

def main():
    # Basic arg check
    if len(sys.argv) < 4:
        print("❌ Internal Error: Insufficient arguments passed to Python.", file=sys.stderr)
        return 1

    input_dir = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    color_count = int(sys.argv[3])

    # Dynamic argument validation
    # Args structure: [0]script [1]in [2]out [3]count [4..4+N]base [4+N..End]target
    expected_argc = 4 + (color_count * 2)
    
    if len(sys.argv) != expected_argc:
        print(f"❌ Argument mismatch. Expected {expected_argc}, got {len(sys.argv)}", file=sys.stderr)
        return 1

    # Slice the argument list dynamically
    pivot = 4 + color_count
    base_rgb = hex_to_rgb(sys.argv[4:pivot])
    target_rgb = hex_to_rgb(sys.argv[pivot:expected_argc])

    files = list(input_dir.glob("*.png"))
    success_count = 0

    for src in files:
        try:
            recolor_image(src, output_dir / src.name, base_rgb, target_rgb)
            print(f"   ✔ {src.name}")
            success_count += 1
        except Exception as e:
            print(f"   ❌ {src.name}: {e}", file=sys.stderr)

    # Fail the script if 0 images were successfully processed
    return 0 if success_count > 0 else 1

if __name__ == "__main__":
    sys.exit(main())
PYTHON_EOF

# --- Cleanup ---
if [[ -d "$CACHE_DIR" ]]; then
    rm -rf -- "$CACHE_DIR"
    log "Cache cleared."
fi

log "Done."
