#!/usr/bin/env bash
# Run Dusky Casts packaging, digest, fallback, and desktop-cleanup unit tests.
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly PROJECT_DIR
readonly TEST_SCRIPT="$PROJECT_DIR/../../arch_setup_scripts/tests/test_casts_source_build.py"
exec python3 "$TEST_SCRIPT" "$@"
