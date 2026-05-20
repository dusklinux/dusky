#!/usr/bin/env bash

COUNTDOWN_DIR="${HOME}/.local/share/countdowns"
mkdir -p "$COUNTDOWN_DIR"

# Internal: get the file path for a named countdown
_countdown_file() {
    echo "$COUNTDOWN_DIR/${1:-default}.countdown"
}

# Internal: read and validate the end timestamp from a countdown file
# Returns the integer end_time, or returns 1 if missing/invalid/expired
_countdown_read() {
    local file
    file=$(_countdown_file "$1")
    [[ -f "$file" ]] || return 1

    local end_time
    end_time=$(< "$file")

    # Validate it's a plain integer
    [[ "$end_time" =~ ^[0-9]+$ ]] || { rm -f "$file"; return 1; }

    local now
    now=$(date +%s)

    if (( now >= end_time )); then
        rm -f "$file"
        return 1
    fi

    echo "$end_time"
}

# Set a countdown by name.
# Usage: countdown_set <name> <duration> [s|m|h]
# Unit defaults to seconds. Examples:
#   countdown_set my-lock 5        → 5 seconds
#   countdown_set my-lock 5 s      → 5 seconds
#   countdown_set my-lock 5 m      → 5 minutes
#   countdown_set my-lock 1 h      → 1 hour
countdown_set() {
    local name="${1:-default}"
    local duration="${2:-5}"
    local unit="${3:-s}"
    local secs

    case "$unit" in
        s|sec|secs|second|seconds) secs=$(( duration )) ;;
        m|min|mins|minute|minutes) secs=$(( duration * 60 )) ;;
        h|hr|hrs|hour|hours)       secs=$(( duration * 3600 )) ;;
        *)
            echo "countdown_set: unknown unit '$unit' (use s, m, or h)" >&2
            return 1
            ;;
    esac

    echo $(( $(date +%s) + secs )) > "$(_countdown_file "$name")"
}

# Legacy alias so existing callers of countdown_disable still work.
# countdown_disable <name> <minutes>
countdown_disable() {
    countdown_set "${1:-default}" "${2:-5}" m
}

# Clear a countdown (re-enable whatever it was blocking)
countdown_clear() {
    rm -f "$(_countdown_file "${1:-default}")"
}

# Legacy alias
countdown_enable() { countdown_clear "$@"; }

# Check if a countdown is active.
# Returns 0 (active) and prints seconds remaining, or returns 1 (inactive/expired).
countdown_check() {
    local end_time
    end_time=$(_countdown_read "${1:-default}") || return 1
    echo $(( end_time - $(date +%s) ))
}

# Returns 0 if countdown is active, 1 if not. No output.
countdown_is_disabled() {
    _countdown_read "${1:-default}" &>/dev/null
}

# Display remaining time in a formatted string.
# Format tokens:
#   %H  — total hours remaining
#   %h  — remainder hours (within days, not useful here but included)
#   %M  — total minutes remaining
#   %m  — remainder minutes (after subtracting full hours)
#   %S  — total seconds remaining
#   %s  — remainder seconds (after subtracting full minutes)
# Default format: %m:%s  (e.g. "04:32")
countdown_display() {
    local name="${1:-default}"
    local fmt="${2:-%m:%s}"

    local remaining
    remaining=$(countdown_check "$name") || return 1

    local total_secs=$remaining
    local total_mins=$(( total_secs / 60 ))
    local total_hours=$(( total_secs / 3600 ))
    local rem_secs=$(( total_secs % 60 ))
    local rem_mins=$(( total_mins % 60 ))

    # Pad single digits with leading zero for display
    local pad_rem_secs pad_rem_mins
    printf -v pad_rem_secs  "%02d" "$rem_secs"
    printf -v pad_rem_mins  "%02d" "$rem_mins"

    local out="$fmt"
    out="${out//%H/$total_hours}"
    out="${out//%M/$total_mins}"
    out="${out//%S/$total_secs}"
    out="${out//%m/$pad_rem_mins}"
    out="${out//%s/$pad_rem_secs}"

    echo "$out"
}

# Convenience alias
countdown_status() { countdown_display "${1:-default}" "${2:-%m:%s}"; }

# Cleans up expired countdown files. Call at script start if you want housekeeping,
# but it's not required — _countdown_read self-cleans on every access.
countdown_init() {
    local name="${1:-default}"
    _countdown_read "$name" &>/dev/null || true
}

# Get the file path (for external scripts that need to write directly)
countdown_get_file() { _countdown_file "$@"; }
