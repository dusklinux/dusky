#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Script: banked_dispatch.sh
# Description: Context-aware workspace dispatcher for Hyprland.
#              Implements "Banked" navigation (1-10, 11-20) based on context.
# Compliance: Bash 5.0+, UWSM safe, ShellCheck clean, Zero-Dep (no jq).
# -----------------------------------------------------------------------------

# --- 1. Safety & Strict Mode ---
set -euo pipefail

# --- 2. Constants ---
# Regex to extract the "id" field from hyprctl's JSON output.
# 'id': followed by space, then a captured group of digits (optional negative sign).
readonly ID_REGEX='"id": *(-?[0-9]+)'

# --- 3. Dependency Check ---
if ! command -v hyprctl &>/dev/null; then
    printf "Fatal: 'hyprctl' command not found. Is Hyprland installed?\n" >&2
    exit 127
fi

# --- 4. Input Validation ---
if [[ $# -lt 2 ]]; then
    printf "Usage: %s <dispatcher> <target>\n" "${0##*/}" >&2
    printf "Example: %s workspace 1\n" "${0##*/}" >&2
    exit 1
fi

readonly dispatcher="$1"
readonly target="$2"

# --- 5. Context Extraction ---
# Capture raw JSON output.
if ! raw_active="$(hyprctl activeworkspace -j 2>/dev/null)"; then
    printf "Error: Failed to retrieve active workspace.\n" >&2
    exit 1
fi

# Parse ID using Bash Regex (No jq required).
if [[ "${raw_active}" =~ ${ID_REGEX} ]]; then
    readonly curr_id="${BASH_REMATCH[1]}"
else
    printf "Error: Could not parse workspace ID from hyprctl.\n" >&2
    exit 1
fi

# --- 6. Dispatch Logic ---

# Translate old-style dispatcher + target into a Lua expression for Hyprland's
# Lua config mode. hyprctl dispatch evaluates the body as Lua.
lua_dispatch() {
    local disp="$1"
    local tgt="$2"
    local lua_tgt

    # Numeric targets are unquoted; named/relative targets are quoted strings.
    if [[ "${tgt}" =~ ^-?[0-9]+$ ]]; then
        lua_tgt="${tgt}"
    else
        lua_tgt="\"${tgt}\""
    fi

    case "${disp}" in
        workspace)
            exec hyprctl dispatch "hl.dsp.focus({workspace=${lua_tgt}})"
            ;;
        movetoworkspace)
            exec hyprctl dispatch "hl.dsp.window.move({workspace=${lua_tgt}})"
            ;;
        movetoworkspacesilent)
            exec hyprctl dispatch "hl.dsp.window.move({workspace=${lua_tgt}, silent=true})"
            ;;
        *)
            exec hyprctl dispatch "${disp}" "${tgt}"
            ;;
    esac
}

# Case A: Pass-Through (Relative, Special, Named)
# If the target is NOT a pure positive integer (contains chars, +, -, or is empty),
# we pass it directly without doing math.
if [[ ! "${target}" =~ ^[0-9]+$ ]]; then
    lua_dispatch "${dispatcher}" "${target}"
fi

# Case B: Banked Navigation (Pure Integer)
# logic: Determine which "bank" of 10 we are in, then add the target.

# If on a Special Workspace (scratchpad has negative ID), assume Bank 0.
if [[ "${curr_id}" -lt 1 ]]; then
    bank_base=0
else
    # Integer division: 1-10 -> 0, 11-20 -> 1, 21-30 -> 2
    bank_base=$(( (curr_id - 1) / 10 ))
fi

# Calculate Final Target
final_target=$(( (bank_base * 10) + target ))

# Execute and replace process
lua_dispatch "${dispatcher}" "${final_target}"
