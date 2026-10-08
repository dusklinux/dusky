#!/usr/bin/env bash
# Use the same package/native selection as the setup profiles; never launch the UI.
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly PROJECT_DIR
readonly SETUP_SCRIPT="$PROJECT_DIR/../../arch_setup_scripts/scripts/153_dusky_center_setup.py"
exec python3 "$SETUP_SCRIPT" "$@"
