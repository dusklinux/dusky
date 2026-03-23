#!/usr/bin/env bash
# ~/user_scripts/btrfs_snapshots/cc/bash_wrapper_for_cc.sh
# Finds matching snapshots for Root and Home by exact Date and atomic swaps both.

set -euo pipefail

TARGET_DATE="$1"

# Resolve script directory securely
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANAGER_SCRIPT="${SCRIPT_DIR}/04_dusky_snapshot_manager.py"

if [[ ! -x "$MANAGER_SCRIPT" ]]; then
    echo "[!] Error: Manager script not found or not executable at $MANAGER_SCRIPT" >&2
    exit 1
fi

# Use Regex field separator for awk to match both ASCII | and Unicode │
HOME_ID=$(snapper -c home list --disable-used-space | awk -F'[|│]' -v d="$TARGET_DATE" 'index($4, d) > 0 {print $1; exit}' | tr -d ' ')
ROOT_ID=$(snapper -c root list --disable-used-space | awk -F'[|│]' -v d="$TARGET_DATE" 'index($4, d) > 0 {print $1; exit}' | tr -d ' ')

# Execute atomic swaps sequentially
if [[ -n "$HOME_ID" ]]; then
    echo "[*] Restoring Home (ID: $HOME_ID)..."
    "$MANAGER_SCRIPT" -c home -R "$HOME_ID"
fi

if [[ -n "$ROOT_ID" ]]; then
    echo "[*] Restoring Root (ID: $ROOT_ID)..."
    "$MANAGER_SCRIPT" -c root -R "$ROOT_ID"
fi
