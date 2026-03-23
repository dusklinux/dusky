#!/usr/bin/env bash
# Finds a coordinated matching Root/Home snapshot pair and stages both restores together.

set -euo pipefail

if (( EUID != 0 )); then
    printf '%s\n' "[!] This script requires root privileges. Please run with sudo." >&2
    exit 1
fi

if (( $# < 1 || $# > 2 )); then
    printf 'Usage: %s TARGET_DATE [TARGET_DESC]\n' "$(basename -- "$0")" >&2
    exit 64
fi

TARGET_DATE=$1
TARGET_DESC=${2-}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
MANAGER_SCRIPT="${SCRIPT_DIR}/04_dusky_snapshot_manager.py"

if [[ ! -f "$MANAGER_SCRIPT" || ! -r "$MANAGER_SCRIPT" ]]; then
    printf '%s\n' "[!] Error: Manager script not found or not readable at $MANAGER_SCRIPT" >&2
    exit 1
fi

TMP_MANAGER="$(mktemp -p /run snapctl-manager.XXXXXX.py)"
trap 'rm -f -- "$TMP_MANAGER"' EXIT
install -m 0600 -- "$MANAGER_SCRIPT" "$TMP_MANAGER"

MANAGER_CMD=(python3 "$TMP_MANAGER")

select_root_id() {
    python3 /dev/fd/3 "$TARGET_DATE" 3<<'PY'
import json
import sys

target_date = sys.argv[1]

try:
    snapshots = json.load(sys.stdin)
except Exception as exc:
    print(f"[!] Fatal: Failed to parse Root snapshot list JSON: {exc}", file=sys.stderr)
    raise SystemExit(1)

matches = [str(item["id"]) for item in snapshots if item.get("raw_date") == target_date]

if len(matches) == 1:
    print(matches[0])
    raise SystemExit(0)

if not matches:
    print(f"[!] Fatal: Could not find Root snapshot for exact date: {target_date}", file=sys.stderr)
    raise SystemExit(1)

print(f"[!] Fatal: Multiple Root snapshots matched exact date: {target_date}", file=sys.stderr)
raise SystemExit(1)
PY
}

select_home_id() {
    python3 /dev/fd/3 "$TARGET_DATE" "$TARGET_DESC" 3<<'PY'
import json
import re
import sys
from datetime import datetime

target_date = sys.argv[1]
target_desc = sys.argv[2]

try:
    snapshots = json.load(sys.stdin)
except Exception as exc:
    print(f"[!] Fatal: Failed to parse Home snapshot list JSON: {exc}", file=sys.stderr)
    raise SystemExit(1)

if not snapshots:
    print("[!] Fatal: Home snapshot list is empty. Cannot perform coordinated restore.", file=sys.stderr)
    raise SystemExit(1)

def minute_prefix(value: str) -> str | None:
    match = re.search(r"^(.*\d{2}:\d{2})", value)
    return match.group(1) if match else None

# --- Attempt 1: Exact Match ---
exact = [str(item["id"]) for item in snapshots if item.get("raw_date") == target_date]
if len(exact) == 1:
    print(exact[0])
    raise SystemExit(0)

if len(exact) > 1:
    print(f"[!] Fatal: Multiple Home snapshots matched exact date: {target_date}", file=sys.stderr)
    raise SystemExit(1)

# --- Attempt 2: Fuzzy Minute Match ---
if target_desc:
    target_minute = minute_prefix(target_date)
    if target_minute:
        fuzzy = [
            str(item["id"])
            for item in snapshots
            if item.get("description") == target_desc
            and minute_prefix(item.get("raw_date", "")) == target_minute
        ]

        if len(fuzzy) == 1:
            print(fuzzy[0])
            raise SystemExit(0)

        if len(fuzzy) > 1:
            print(
                "[!] Fatal: Multiple Home snapshots matched the fuzzy minute+description search; "
                "aborting to avoid restoring the wrong snapshot.",
                file=sys.stderr,
            )
            raise SystemExit(1)

# --- Attempt 3: Mathematical Closest Time Fallback ---
def parse_dt(d_str):
    d_str = str(d_str).strip()
    if not d_str:
        return None
    # Handle Snapper tabular format: "Sun 22 Mar 2026 01:53:16 PM IST"
    tokens = d_str.split()
    if len(tokens) >= 7 and tokens[-1].isalpha():
        try:
            clean = " ".join(tokens[:-1])
            return datetime.strptime(clean, "%a %d %b %Y %I:%M:%S %p")
        except ValueError:
            pass
    # Handle Snapper jsonout format
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(d_str, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(d_str.replace(" ", "T", 1))
    except ValueError:
        return None

target_dt = parse_dt(target_date)
best_diff = float('inf')
best_id = None

if target_dt:
    for s in snapshots:
        s_dt = parse_dt(s.get("raw_date", ""))
        if s_dt:
            diff = abs((s_dt - target_dt).total_seconds())
            if diff < best_diff:
                best_diff = diff
                best_id = str(s["id"])

if best_id is not None:
    print(f"[*] Warning: Could not find exact Home snapshot. Falling back to mathematically closest snapshot (ID {best_id}).", file=sys.stderr)
    print(best_id)
    raise SystemExit(0)

# --- Ultimate Fallback: Most Recent ---
best_id = str(sorted(snapshots, key=lambda x: int(x["id"]))[-1]["id"])
print(f"[*] Warning: Date parsing failed. Falling back to most recent Home snapshot (ID {best_id}).", file=sys.stderr)
print(best_id)
raise SystemExit(0)
PY
}

if ! ROOT_JSON="$("${MANAGER_CMD[@]}" -c root --json -l)"; then
    printf '%s\n' "[!] Fatal: Failed to query Root snapshots." >&2
    exit 1
fi

if ! HOME_JSON="$("${MANAGER_CMD[@]}" -c home --json -l)"; then
    printf '%s\n' "[!] Fatal: Failed to query Home snapshots." >&2
    exit 1
fi

if ! ROOT_ID="$(printf '%s' "$ROOT_JSON" | select_root_id)"; then
    exit 1
fi

if ! HOME_ID="$(printf '%s' "$HOME_JSON" | select_home_id)"; then
    exit 1
fi

printf '%s\n' "[*] Found coordinated snapshot pair: Root=$ROOT_ID Home=$HOME_ID"
printf '%s\n' "[*] Staging coordinated restore for next reboot..."
"${MANAGER_CMD[@]}" --restore-pair root "$ROOT_ID" home "$HOME_ID"
